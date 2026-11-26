#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""정체 감시 장치 (agent watchdog).

작업판(~/.agent-hub/board.json)에서 status=doing 인 카드를 감시하다가
진행 흔적이 끊긴 카드를 슬랙으로 찔러 올린다.

  20분 무진행  -> 1차: 그 카드의 주인에게(스레드가 있으면 그 스레드로, 없으면 #ai-lab)
  40분 무진행  -> 2차: #ai-lab 에 에스컬레이션

같은 카드의 같은 단계는 한 번만 보낸다(상태 파일). 카드가 끝나면 상태에서 지운다.

  agent_watchdog.py --once --dry-run          # 한 바퀴, 슬랙에 안 보내고 출력만
  agent_watchdog.py --once                    # 한 바퀴, 실제 전송
  agent_watchdog.py                           # 60초마다 계속 (launchd 에서는 --once)

표준 라이브러리만 쓴다. 슬랙 토큰이 없어도, 작업판이 깨져 있어도 죽지 않는다.
"""
import argparse
import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

LABEL = 'ai.yuri.agent-watchdog'
CHANNEL_DEFAULT = 'C0CLN0CN4CX'          # #ai-lab
BOARD_CANDIDATES = ('~/agent-hub/board.json', '~/factory/hub/board.json')
STATE_DEFAULT = '~/.agent-watchdog/state.json'
LOG_DEFAULT = '~/.agent-watchdog/events.jsonl'
CONFIG_DEFAULT = '~/.agent-watchdog/config.json'

# '마지막 진행 흔적' 후보. 앞에서부터 읽어 처음 파싱되는 값을 쓴다.
TRACE_FIELDS = ('updated_at', 'last_progress_at', 'last_log_at', 'last_seen_at',
                'last_activity_at', 'started_at', 'claimed_at', 'created_at')

# 카드 owner -> 슬랙 user id (1차 메시지에서 부를 사람). config.json 의 owners 가 이긴다.
OWNER_SLACK = {
    'codex': 'U0CM37GJN82',      # codex-tbd
    'dot': 'U0CM9CXG200',        # tbd-dot
    'tbd-dot': 'U0CM9CXG200',
    'instinct': 'U0C83BSP467',   # Instinct
    'human': 'U08DLN71LEA',      # tbd (사람)
}

WATCH_STATUS = 'doing'
DONE_STATUSES = ('done',)
LOGROTATE_BYTES = 5 * 1024 * 1024
HTTP_TIMEOUT = 15

_THREAD_RE = re.compile(r'slack_thread=([A-Z0-9]+)/(\d+(?:\.\d+)?)')
_TOKEN_RE = re.compile(r'^xox[abposre]-?[A-Za-z0-9-]{8,}$')


# ---------------------------------------------------------------- 시간 유틸
def now_local():
    return dt.datetime.now().astimezone()


def iso(d):
    return d.isoformat(timespec='seconds') if d else None


def _from_epoch(v):
    if v > 1e11:            # 밀리초
        v = v / 1000.0
    try:
        return dt.datetime.fromtimestamp(v).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def parse_time(value):
    """허브가 쓰는 ISO8601(+09:00)을 1순위로, epoch/여러 표기를 관대하게 받는다."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    head = s.replace('.', '', 1).lstrip('-')
    if head.isdigit():
        try:
            return _from_epoch(float(s))
        except ValueError:
            return None
    t = s
    if t[-1] in 'Zz':
        t = t[:-1] + '+00:00'
    try:
        d = dt.datetime.fromisoformat(t)
        return d.astimezone() if d.tzinfo is None else d
    except ValueError:
        pass
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d',
                '%Y/%m/%d %H:%M:%S', '%Y.%m.%d %H:%M:%S'):
        try:
            return dt.datetime.strptime(s, fmt).astimezone()
        except ValueError:
            continue
    return None


def human_min(v):
    return '%d분' % int(round(v))


# ---------------------------------------------------------------- 기록
def _rotate(path):
    try:
        if path.exists() and path.stat().st_size > LOGROTATE_BYTES:
            os.replace(str(path), str(path) + '.1')
    except OSError:
        pass


def log_event(log_path, **fields):
    """JSON Lines. 실패해도 조용히 넘어간다(감시 장치가 로그 때문에 죽으면 안 된다)."""
    try:
        p = Path(os.path.expanduser(log_path))
        p.parent.mkdir(parents=True, exist_ok=True)
        _rotate(p)
        rec = {'ts': iso(now_local())}
        rec.update(fields)
        with open(str(p), 'a', encoding='utf-8') as f:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    except (OSError, ValueError):
        pass


def say(text, cfg):
    """launchd 로그(표준출력) 한 줄."""
    print('%s %s' % (iso(now_local()), text))
    sys.stdout.flush()


# ---------------------------------------------------------------- 작업판
def resolve_board(explicit):
    if explicit:
        return Path(os.path.expanduser(explicit)), 'argument'
    for cand in BOARD_CANDIDATES:
        p = Path(os.path.expanduser(cand))
        if p.exists():
            return p, 'default'
    return Path(os.path.expanduser(BOARD_CANDIDATES[0])), 'missing'


def last_log_time(board_path, task_id):
    """log.jsonl 에서 그 카드의 마지막 기록 시각. 카드에 시각 필드가 없을 때 쓴다."""
    p = Path(board_path).parent / 'log.jsonl'
    if not p.exists():
        return None
    try:
        size = p.stat().st_size
        with open(str(p), 'rb') as f:
            if size > 512 * 1024:
                f.seek(-512 * 1024, os.SEEK_END)
                f.readline()          # 잘린 첫 줄 버리기
            data = f.read()
    except OSError:
        return None
    best = None
    tid = str(task_id)
    for raw in data.decode('utf-8', 'replace').splitlines():
        if not raw.strip() or tid not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(rec, dict) or str(rec.get('task', '')) != tid:
            continue
        d = parse_time(rec.get('ts') or rec.get('at'))
        if d and (best is None or d > best):
            best = d
    return best


def task_trace(task, board_path, board_mtime):
    """(마지막 흔적 시각, 그 값을 어디서 얻었는지). 못 찾으면 board mtime."""
    for field in TRACE_FIELDS:
        if field in task:
            d = parse_time(task.get(field))
            if d:
                return d, field
    d = last_log_time(board_path, task.get('id'))
    if d:
        return d, 'log.jsonl'
    if board_mtime:
        return board_mtime, 'board.mtime'
    return None, 'unknown'


def thread_of(task):
    """ROUTING: 카드 notes 첫 줄 slack_thread=<channel>/<ts>."""
    notes = task.get('notes')
    if not isinstance(notes, str):
        return None
    m = _THREAD_RE.search(notes)
    return (m.group(1), m.group(2)) if m else None


def title_of(task):
    for k in ('title', 'goal', 'id'):
        v = task.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip().splitlines()[0][:120]
    return '(제목 없음)'


# ---------------------------------------------------------------- 상태
def load_json(path, default):
    try:
        with open(str(path), encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + '.tmp')
    with open(str(tmp), 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write('\n')
    os.replace(str(tmp), str(p))


# ---------------------------------------------------------------- 슬랙
def find_token_in(obj):
    """credentials.json 모양을 모르므로 통째로 훑어 xox* 토큰을 찾는다."""
    found = []

    def walk(node, key):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            s = node.strip()
            if _TOKEN_RE.match(s):
                score = 3 if s.startswith('xoxb') else 2 if s.startswith('xoxp') else 1
                if 'token' in key.lower():
                    score += 1
                found.append((score, s))

    walk(obj, '')
    if not found:
        return None
    found.sort(key=lambda x: -x[0])
    return found[0][1]


def read_token():
    for key in ('SLACK_BOT_TOKEN', 'SLACK_TOKEN'):
        v = os.environ.get(key)
        if v and v.strip():
            return v.strip(), 'env:' + key
    for rel in ('~/.agent-watchdog/token', '~/.slack/token'):
        p = Path(os.path.expanduser(rel))
        try:
            if p.exists():
                s = p.read_text(encoding='utf-8').strip().splitlines()
                if s and _TOKEN_RE.match(s[0].strip()):
                    return s[0].strip(), rel
        except OSError:
            pass
    p = Path(os.path.expanduser('~/.slack/credentials.json'))
    try:
        if p.exists():
            tok = find_token_in(json.loads(p.read_text(encoding='utf-8')))
            if tok:
                return tok, '~/.slack/credentials.json'
    except (OSError, ValueError):
        pass
    return None, None


def api_post(token, channel, text, thread_ts=None):
    data = {'channel': channel, 'text': text,
            'unfurl_links': 'false', 'unfurl_media': 'false', 'link_names': 'true'}
    if thread_ts:
        data['thread_ts'] = thread_ts
    req = urllib.request.Request(
        'https://slack.com/api/chat.postMessage',
        data=urllib.parse.urlencode(data).encode('utf-8'),
        headers={'Authorization': 'Bearer ' + token,
                 'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8'})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            res = json.loads(r.read().decode('utf-8', 'replace'))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return {'ok': False, 'error': 'transport:%s' % type(e).__name__}
    return res if isinstance(res, dict) else {'ok': False, 'error': 'bad_response'}


RELAY_PROBE = {}


def relay_usable(relay_path):
    """~/.slack_relay.py 를 재사용해도 되는지 소스만 읽어 판단한다: (쓸 수 있나, 스레드 지정이 되나).

    --help 로 물어보지 않는다: 위치 인자를 메시지로 받는 스크립트라면 '--help' 가
    그대로 슬랙에 올라갈 수 있다. 소스에 --channel 과 --text/--message 가 있을 때만 쓴다.
    """
    try:
        mtime = os.path.getmtime(relay_path)
    except OSError:
        return False, False
    key = (relay_path, mtime)
    if key in RELAY_PROBE:
        return RELAY_PROBE[key]
    try:
        with open(relay_path, encoding='utf-8', errors='replace') as f:
            src = f.read()
        ok = bool(re.search(r"add_argument\(\s*['\"]--channel['\"]", src)) and \
             bool(re.search(r"add_argument\(\s*['\"](--text|--message)['\"]", src))
        threads = bool(re.search(r"add_argument\(\s*['\"]--thread[-_]ts['\"]", src))
    except OSError:
        ok, threads = False, False
    RELAY_PROBE[key] = (ok, threads)
    return ok, threads


def deliver(channel, text, thread_ts, cfg, log_path):
    """(성공, 경로, 오류). 어떤 경우에도 예외를 밖으로 던지지 않는다."""
    if cfg.dry_run:
        return True, 'dry-run', None

    # 0) 테스트·대체 전송 훅: /bin/sh -c CMD, 본문은 env 로도 준다.
    cmd = cfg.send_cmd or os.environ.get('AGENT_WATCHDOG_SEND_CMD')
    if cmd:
        env = dict(os.environ)
        env.update({'AGENT_WATCHDOG_CHANNEL': channel or '',
                    'AGENT_WATCHDOG_THREAD': thread_ts or '',
                    'AGENT_WATCHDOG_TEXT': text})
        try:
            p = subprocess.run(['/bin/sh', '-c', cmd], input=text, env=env,
                               capture_output=True, text=True, timeout=30)
            if p.returncode == 0:
                return True, 'cmd', None
            err = (p.stderr or p.stdout or '').strip()[:200] or 'exit %d' % p.returncode
        except (OSError, subprocess.SubprocessError) as e:
            err = 'cmd:%s' % type(e).__name__
        log_event(log_path, event='send_fallback', via='cmd', error=err)
    else:
        err = None

    # 1) 기존 ~/slack_relay.py (인터페이스가 맞을 때만).
    #    스레드로 보내야 하는데 릴레이가 스레드를 못 받치면, 토큰이 있을 때만 직접 API 로 간다.
    token, token_src = read_token()
    relay = Path(os.path.expanduser('~/slack_relay.py'))
    if relay.exists():
        usable, threads = relay_usable(str(relay))
        if usable and (threads or not thread_ts or not token):
            py = sys.executable or '/usr/bin/python3'
            try:
                args = [py, str(relay), '--channel', channel, '--text', text]
                if threads and thread_ts:
                    args += ['--thread-ts', thread_ts]
                p = subprocess.run(args, capture_output=True, text=True, timeout=30)
                if p.returncode == 0:
                    return True, 'relay', None
                err = (p.stderr or p.stdout or '').strip()[:200] or 'relay exit %d' % p.returncode
            except (OSError, subprocess.SubprocessError) as e:
                err = 'relay:%s' % type(e).__name__
            log_event(log_path, event='send_fallback', via='relay', error=err)

    # 2) 토큰으로 chat.postMessage 직접
    if not token:
        log_event(log_path, event='no_token', channel=channel, chars=len(text))
        return False, 'none', err or 'no_slack_token'
    res = api_post(token, channel, text, thread_ts)
    if res.get('ok'):
        return True, 'api:%s' % token_src, None
    err = str(res.get('error', 'unknown'))
    log_event(log_path, event='slack_error', channel=channel, error=err, via=token_src)
    return False, 'api:%s' % token_src, err


# ---------------------------------------------------------------- 메시지
def compose(stage, task, trace, source, age_min, cfg):
    tid = str(task.get('id', '?'))
    owner = str(task.get('owner') or '미지정')
    nxt = task.get('next_step')
    nxt = nxt.strip().splitlines()[0][:120] if isinstance(nxt, str) and nxt.strip() else ''
    trace_txt = trace.strftime('%Y-%m-%d %H:%M') if trace else '알 수 없음'
    mention = ''
    if cfg.mention:
        uid = cfg.owners.get(owner)
        if uid:
            mention = '<@%s> ' % uid

    if stage == 1:
        lines = [':eyes: %s[정체 감시] `%s` %s' % (mention, tid, title_of(task)),
                 '담당 %s · %s · 마지막 흔적 %s (%s) · %s째 조용함'
                 % (owner, WATCH_STATUS, trace_txt, source, human_min(age_min)),
                 '지금 어디까지 됐고 뭐가 막혔는지 한 줄로 답해라.']
        if nxt:
            lines.append('카드상 다음 한 수: %s' % nxt)
        return '\n'.join(lines) + '\n'

    lines = [':rotating_light: %s[정체 감시 · 2차] `%s` %s'
             % (('<@%s> ' % cfg.owners['human']) if (cfg.mention and cfg.owners.get('human')) else '',
                tid, title_of(task)),
             '담당 %s · %s · %s 무진행 (2차 기준 %s) · 마지막 흔적 %s (%s)'
             % (owner, WATCH_STATUS, human_min(age_min), human_min(cfg.escalate_min),
                trace_txt, source)]
    if nxt:
        lines.append('카드상 다음 한 수: %s' % nxt)
    lines.append('1차 찔러라에 응답이 없었다. 사람이 판을 보고 담당을 바꾸거나 내려야 한다.')
    return '\n'.join(lines) + '\n'


# ---------------------------------------------------------------- 한 바퀴
class Cfg(object):
    def __init__(self, a):
        conf = load_json(Path(os.path.expanduser(CONFIG_DEFAULT)), {})
        if not isinstance(conf, dict):
            conf = {}
        self.channel = a.channel or conf.get('channel') or CHANNEL_DEFAULT
        self.warn_min = float(a.warn_min if a.warn_min is not None else conf.get('warn_min', 20))
        self.escalate_min = float(a.escalate_min if a.escalate_min is not None
                                  else conf.get('escalate_min', 40))
        if self.escalate_min < self.warn_min:
            self.escalate_min = self.warn_min
        self.state = a.state or conf.get('state') or STATE_DEFAULT
        self.log = a.log or conf.get('log') or LOG_DEFAULT
        self.dry_run = bool(a.dry_run)
        self.mention = bool(conf.get('mention', True)) and not a.no_mention
        self.max_per_run = int(a.max_per_run if a.max_per_run is not None
                               else conf.get('max_per_run', 5))
        self.retry_sec = float(a.retry_sec if a.retry_sec is not None
                               else conf.get('retry_sec', 300))
        self.send_cmd = a.send_cmd
        self.verbose = bool(a.verbose)
        owners = dict(OWNER_SLACK)
        if isinstance(conf.get('owners'), dict):
            owners.update({str(k): str(v) for k, v in conf['owners'].items()})
        self.owners = owners
        self.now = parse_time(a.now) if a.now else None


def load_state(path, log_path):
    st = load_json(Path(os.path.expanduser(path)), None)
    if not isinstance(st, dict):
        return {'version': 1, 'tasks': {}}
    if not isinstance(st.get('tasks'), dict):
        st['tasks'] = {}
    st.setdefault('version', 1)
    return st


def run_once(a):
    cfg = Cfg(a)
    now = cfg.now or now_local()
    board_path, how = resolve_board(a.board)
    log_path = cfg.log
    state_path = Path(os.path.expanduser(cfg.state))

    if not board_path.exists():
        log_event(log_path, event='board_missing', board=str(board_path))
        say('board 없음: %s (%s)' % (board_path, how), cfg)
        return 0

    pause = board_path.parent / 'PAUSE'
    if pause.exists():
        log_event(log_path, event='paused', board=str(board_path))
        say('PAUSE 파일이 있어 쉰다 (%s)' % pause, cfg)
        return 0

    try:
        mtime = dt.datetime.fromtimestamp(board_path.stat().st_mtime).astimezone()
        with open(str(board_path), encoding='utf-8') as f:
            board = json.load(f)
    except (OSError, ValueError) as e:
        log_event(log_path, event='board_unreadable', board=str(board_path),
                  error=type(e).__name__)
        say('board를 못 읽었다: %s' % type(e).__name__, cfg)
        return 0

    tasks = board.get('tasks') if isinstance(board, dict) else None
    if not isinstance(tasks, list):
        log_event(log_path, event='board_malformed', board=str(board_path))
        say('board 형식이 예상과 다르다(tasks 없음)', cfg)
        return 0

    state = load_state(cfg.state, log_path)
    seen = state['tasks']
    doing = {}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        if str(t.get('status', '')).strip().lower() == WATCH_STATUS:
            doing[str(t.get('id', '?'))] = t

    sent = 0
    tries = 0
    for tid, task in sorted(doing.items()):
        trace, source = task_trace(task, board_path, mtime)
        if trace is None:
            log_event(log_path, event='no_trace', task=tid)
            continue
        age = (now - trace).total_seconds() / 60.0
        if age < 0:
            age = 0.0
        target = 2 if age >= cfg.escalate_min else (1 if age >= cfg.warn_min else 0)

        prev = seen.get(tid) if isinstance(seen.get(tid), dict) else {}
        same_trace = iso(trace) == prev.get('trace')
        prev_stage = int(prev.get('stage') or 0) if same_trace else 0

        if target < 1:
            if prev and same_trace:
                pass                      # 정상 진행 중: 그대로 둔다
            continue
        if target <= prev_stage:
            if cfg.verbose:
                say('중복 억제 %s 단계%d (이미 보냈다)' % (tid, target), cfg)
            continue
        # 전송이 실패했으면 단계를 올리지 않고, 5분 뒤에 다시 시도한다(토큰 고칠 시간).
        last_try = parse_time(prev.get('last_attempt')) if same_trace else None
        if last_try and (now - last_try).total_seconds() < cfg.retry_sec:
            if cfg.verbose:
                say('재시도 대기 %s (마지막 시도 %s)' % (tid, iso(last_try)), cfg)
            continue
        if tries >= cfg.max_per_run:
            log_event(log_path, event='throttled', task=tid, stage=target,
                      age_min=round(age, 1))
            say('이번 회차 전송 상한(%d)에 걸려 %s 는 다음에' % (cfg.max_per_run, tid), cfg)
            continue

        text = compose(target, task, trace, source, age, cfg)
        channel, thread_ts = cfg.channel, None
        th = thread_of(task)
        if target == 1 and th:
            channel, thread_ts = th[0], th[1]

        ok, via, err = deliver(channel, text, thread_ts, cfg, log_path)
        tries += 1
        sent += 1 if ok else 0
        stage_name = 'warn' if target == 1 else 'escalate'
        log_event(log_path, event=stage_name, task=tid, owner=task.get('owner'),
                  age_min=round(age, 1), trace=iso(trace), trace_source=source,
                  channel=channel or '', thread=thread_ts or '', sent=bool(ok),
                  via=via, error=err or '')
        if cfg.dry_run:
            print('DRY [%s] %s %s age=%dm trace=%s src=%s → channel=%s thread=%s'
                  % (stage_name, tid, task.get('owner') or '-', int(round(age)),
                     iso(trace), source, channel, thread_ts or '-'))
            print(text)
            print('')
        else:
            say('%s %s(%s) age=%dm → %s %s'
                % (stage_name, tid, task.get('owner') or '-', int(round(age)),
                   'sent' if ok else 'FAILED', via if ok else (err or '-')), cfg)

        if not cfg.dry_run:
            entry = prev if same_trace else {'first_seen': iso(now)}
            entry.update({'trace': iso(trace), 'owner': task.get('owner'),
                          'last_attempt': iso(now)})
            if ok:
                entry.update({'stage': max(target, prev_stage), 'last_sent': iso(now),
                              'last_result': 'sent', 'attempts': 0})
            else:
                entry.update({'stage': prev_stage if same_trace else 0,
                              'attempts': int(prev.get('attempts') or 0) + 1,
                              'last_result': 'failed:%s' % (err or via)})
            seen[tid] = entry
            try:
                save_json(state_path, state)
            except OSError as e:
                log_event(log_path, event='state_error', error=type(e).__name__)

    # 정리: doing 이 아니게 된 카드는 상태에서 지운다(다시 doing 이 되면 처음부터).
    stale = [tid for tid in list(seen.keys()) if tid not in doing]
    for tid in stale:
        seen.pop(tid, None)
        if a.verbose:
            say('상태 정리 %s (더 이상 doing 아님/사라짐)' % tid, cfg)

    if not cfg.dry_run and (sent or stale):
        state['updated_at'] = iso(now)
        try:
            save_json(state_path, state)
        except OSError:
            pass

    log_event(log_path, event='run', board=str(board_path), doing=len(doing),
              sent=sent, dry_run=cfg.dry_run)
    if not cfg.dry_run:
        say('run board=%s doing=%d sent=%d' %
            (board_path.name, len(doing), sent), cfg)
    return 0


# ---------------------------------------------------------------- main
def parse_args(argv):
    ap = argparse.ArgumentParser(
        description='작업판에서 멈춘 카드를 찾아 슬랙으로 찔러 올린다.')
    ap.add_argument('--once', action='store_true', help='한 바퀴만 돌고 끝난다')
    ap.add_argument('--dry-run', action='store_true', help='슬랙에 보내지 않고 출력만')
    ap.add_argument('--board', help='board.json 경로 (기본: ~/agent-hub/board.json)')
    ap.add_argument('--state', help='상태 파일 경로 (기본: ~/.agent-watchdog/state.json)')
    ap.add_argument('--log', help='이벤트 로그 경로 (기본: ~/.agent-watchdog/events.jsonl)')
    ap.add_argument('--warn-min', type=float, help='1차 기준 분 (기본 20)')
    ap.add_argument('--escalate-min', type=float, help='2차 기준 분 (기본 40)')
    ap.add_argument('--channel', help='에스컬레이션 채널 (기본 #ai-lab C0CLN0CN4CX)')
    ap.add_argument('--interval', type=float, default=60, help='반복 주기 초 (기본 60)')
    ap.add_argument('--max-per-run', type=int, help='한 회차 최대 전송 수 (기본 5)')
    ap.add_argument('--retry-sec', type=float, help='전송 실패 후 재시도 간격 초 (기본 300)')
    ap.add_argument('--send-cmd', help='슬랙 대신 실행할 명령 (테스트용, /bin/sh -c)')
    ap.add_argument('--no-mention', action='store_true', help='담당 @멘션을 붙이지 않는다')
    ap.add_argument('--now', help='기준 시각 (검증용, ISO8601)')
    ap.add_argument('--verbose', action='store_true', help='중복 억제·상태 정리도 출력')
    return ap.parse_args(argv)


def main(argv=None):
    a = parse_args(argv)
    # 겹쳐 돌지 않게 (launchd StartInterval + 수동 실행)
    lock_path = Path(os.path.expanduser('~/.agent-watchdog/.lock'))
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(str(lock_path), 'a+')
    except OSError:
        lock_file = None
    if lock_file is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            return 0
    try:
        if a.once:
            return run_once(a)
        while True:
            try:
                run_once(a)
            except KeyboardInterrupt:
                raise
            except Exception as e:                    # 어떤 경우에도 죽지 않는다
                log_event(a.log or LOG_DEFAULT, event='error',
                          error=type(e).__name__, detail=str(e)[:200])
                say('오류 %s: %s' % (type(e).__name__, str(e)[:200]), None)
            time.sleep(max(5.0, float(a.interval)))
    except KeyboardInterrupt:
        return 0
    finally:
        if lock_file is not None:
            lock_file.close()


if __name__ == '__main__':
    sys.exit(main())
