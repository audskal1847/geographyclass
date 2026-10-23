# -*- coding: utf-8 -*-
"""
수업 웹앱용 안전 저장 엔진 (storage.py)

기존 load_json / save_json 방식의 문제
  1) 파일을 못 읽으면 빈 {} 를 돌려주고, 다음 저장 때 전체 파일을 덮어써 모든 학생 자료가 사라짐
  2) 여러 학생이 동시에 저장하면 '읽기 -> 수정 -> 전체 쓰기' 사이에 다른 학생의 저장이 덮어써짐
  3) 저장 중 서버가 끊기면 반쯤 쓰다 만 파일이 남음
  4) 서버(Streamlit Cloud)가 재시작되면 서버 안의 파일이 초기화됨

이 모듈의 해결 방식
  - 모든 데이터를 메모리에 한 벌만 두고(RLock 보호), 저장은 '바뀐 항목만' 반영합니다.
      * 학습 데이터: 학생 1명 x 수행평가 1개 단위
      * 회원 정보: 회원 1명 단위
      * 설정: 최상위 항목(공지, 기한 등) 단위
    -> 다른 학생이 동시에 저장해도 서로의 내용을 절대 덮어쓰지 않습니다.
  - 저장할 때마다 변경 내용을 저널(journal.jsonl)에 즉시 기록(fsync)하고,
    전체 파일은 임시 파일에 쓴 뒤 한 번에 교체(원자적 쓰기)합니다.
  - 파일이 손상되어 읽을 수 없으면 절대 빈 데이터로 시작하지 않고, 최신 백업에서 불러옵니다.
  - 전체 스냅샷(백업)은 저장할 때마다가 아니라 변경이 있을 때 최대 5분에 한 번 만듭니다.
  - (선택) Google Sheets 에 모든 항목을 실시간으로 복제해 두고, 서버가 초기화되면 시트에서 복원합니다.
"""
import copy
import datetime
import json
import os
import threading
import time
import traceback

SEP = "||"                    # 2단계 키 구분자 (학생키||수행평가명)
CHUNK = 40000                 # 구글 시트 셀 1칸 최대 5만 자 -> 4만 자씩 분할
MAX_REQ_CHARS = 1_500_000     # 시트 API 요청 1회당 최대 전송량(여유 있게)
SNAPSHOT_EVERY = 300          # 변경이 있으면 최대 5분마다 전체 스냅샷
SNAPSHOT_KEEP = 48            # 스냅샷 보관 개수
FLUSH_EVERY = 2.0             # 전체 파일 반영 주기(초)
SHEET_PUSH_EVERY = 4.0        # 구글 시트 반영 주기(초)


def _now():
    return time.time()


def _kst_str(ts=None):
    ts = _now() if ts is None else ts
    return (datetime.datetime.utcfromtimestamp(ts) + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S")


def _read_json_file(path):
    """BOM 이 있든 없든 읽는다. 실패하면 예외를 그대로 올린다(빈 값으로 대체하지 않음)."""
    with open(path, "rb") as f:
        raw = f.read()
    last = None
    for enc in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return json.loads(raw.decode(enc).lstrip("﻿"))
        except Exception as e:  # noqa
            last = e
    raise last


def _atomic_write(path, text):
    tmp = f"{path}.tmp{os.getpid()}_{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class TrackedDict(dict):
    """load() 가 돌려주는 사본. 불러온 시점의 원본(base)을 기억해 두었다가
    save() 할 때 '이 사용자가 실제로 바꾼 항목'만 골라 반영하는 데 쓴다."""
    __slots__ = ("_ns", "_base")


class StorageNotReady(Exception):
    pass


# =====================================================================
# Google Sheets 미러
# =====================================================================
class SheetsMirror:
    """시트 구성: 워크시트 이름 = 네임스페이스(users / data / config / drafts)
       열: A 키 | B 수정시각(epoch) | C 삭제여부(1/0) | D 분할 개수 | E 수정시각(한국시간) | F~ JSON 조각"""
    HEADER = ["key", "updated_at", "deleted", "chunks", "updated_kst"]
    FIRST_DATA_COL = 6

    def __init__(self, service_account_info, sheet_url, client=None):
        if client is None:
            import gspread
            client = gspread.service_account_from_dict(dict(service_account_info))
        self.client = client
        self.sh = client.open_by_url(sheet_url)
        self.ws = {}
        self.rowmap = {}     # ns -> {key: row}
        self.next_row = {}   # ns -> 다음 빈 행 번호
        self.cols = {}       # ns -> 현재 열 개수
        self.rows = {}       # ns -> 현재 행 개수

    def _get_ws(self, ns):
        if ns in self.ws:
            return self.ws[ns]
        try:
            ws = self.sh.worksheet(ns)
        except Exception:
            ws = self.sh.add_worksheet(title=ns, rows=200, cols=20)
            ws.update(range_name="A1:E1", values=[self.HEADER], value_input_option="RAW")
        self.ws[ns] = ws
        return ws

    def fetch_all(self, ns):
        """반환: {key: (ts, deleted, value_or_None)}"""
        ws = self._get_ws(ns)
        values = ws.get_all_values()
        out, rowmap = {}, {}
        for i, row in enumerate(values[1:], start=2):
            if not row or not row[0]:
                continue
            key = row[0]
            try:
                ts = float(row[1]) if len(row) > 1 and row[1] != "" else 0.0
            except ValueError:
                ts = 0.0
            deleted = len(row) > 2 and str(row[2]).strip() in ("1", "TRUE", "True")
            try:
                n = int(row[3]) if len(row) > 3 and row[3] != "" else 0
            except ValueError:
                n = 0
            val = None
            if not deleted:
                text = "".join(row[self.FIRST_DATA_COL - 1:self.FIRST_DATA_COL - 1 + n])
                try:
                    val = json.loads(text) if text else None
                except Exception:
                    # 손상된 행은 건너뛴다(로컬 값이 살아남도록)
                    continue
            # 같은 키가 여러 행에 있으면 최신 것만
            if key in out and out[key][0] > ts:
                continue
            out[key] = (ts, deleted, val)
            rowmap[key] = i
        self.rowmap[ns] = rowmap
        self.next_row[ns] = max(len(values) + 1, 2)
        self.rows[ns] = getattr(ws, "row_count", len(values) + 1)
        self.cols[ns] = getattr(ws, "col_count", 26)
        return out

    def _ensure_size(self, ns, need_rows, need_cols):
        ws = self._get_ws(ns)
        if need_rows > self.rows.get(ns, 0):
            add = max(need_rows - self.rows.get(ns, 0), 100)
            ws.add_rows(add)
            self.rows[ns] = self.rows.get(ns, 0) + add
        if need_cols > self.cols.get(ns, 0):
            add = need_cols - self.cols.get(ns, 0) + 5
            ws.add_cols(add)
            self.cols[ns] = self.cols.get(ns, 0) + add

    @staticmethod
    def _col_letter(n):
        s = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def push(self, ns, items):
        """items: [(key, ts, deleted, value)] -> 시트에 기록. 실패 시 예외."""
        if not items:
            return
        ws = self._get_ws(ns)
        if ns not in self.rowmap:
            self.fetch_all(ns)
        rowmap = self.rowmap[ns]
        batches, cur, cur_size = [], [], 0
        max_cols = 0
        planned = []
        for key, ts, deleted, value in items:
            if key in rowmap:
                r = rowmap[key]
            else:
                r = self.next_row[ns]
                self.next_row[ns] += 1
                rowmap[key] = r
            text = "" if deleted else json.dumps(value, ensure_ascii=False)
            chunks = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)] if text else []
            head = [key, ts, 1 if deleted else 0, len(chunks), _kst_str(ts)]
            planned.append((r, head, chunks))
            max_cols = max(max_cols, self.FIRST_DATA_COL - 1 + len(chunks))
        self._ensure_size(ns, max(r for r, _, _ in planned) + 1, max_cols + 1)
        for r, head, chunks in planned:
            # 머리 5칸
            seg = [{"range": f"A{r}:E{r}", "values": [head]}]
            # 조각들은 요청 크기 제한을 넘지 않도록 나누어 보냄
            col = self.FIRST_DATA_COL
            group, gsize = [], 0
            for ch in chunks:
                if group and gsize + len(ch) > MAX_REQ_CHARS:
                    c1, c2 = self._col_letter(col), self._col_letter(col + len(group) - 1)
                    seg.append({"range": f"{c1}{r}:{c2}{r}", "values": [group]})
                    col += len(group)
                    group, gsize = [], 0
                group.append(ch)
                gsize += len(ch)
            if group:
                c1, c2 = self._col_letter(col), self._col_letter(col + len(group) - 1)
                seg.append({"range": f"{c1}{r}:{c2}{r}", "values": [group]})
            for s in seg:
                size = sum(len(str(v)) for v in s["values"][0])
                if cur and cur_size + size > MAX_REQ_CHARS:
                    batches.append(cur)
                    cur, cur_size = [], 0
                cur.append(s)
                cur_size += size
        if cur:
            batches.append(cur)
        for b in batches:
            ws.batch_update(b, value_input_option="RAW")


# =====================================================================
# 저장소 본체
# =====================================================================
class Store:
    def __init__(self, paths, depth, data_dir=".", backup_dir="backups", sheets=None, sheets_factory=None,
                 start_thread=True, snapshot_names=None):
        """
        paths: {ns: 파일경로}, depth: {ns: 1|2}
        sheets_factory: 호출하면 SheetsMirror 를 돌려주는 함수(연결 실패 시 예외)
        snapshot_names: 스냅샷 파일 안에서 쓸 이름 {ns: 'users'|'learning_data'|'config'}
        """
        self.lock = threading.RLock()
        self.paths = dict(paths)
        self.depth = dict(depth)
        self.data_dir = data_dir
        self.backup_dir = backup_dir
        os.makedirs(backup_dir, exist_ok=True)
        self.meta_path = os.path.join(data_dir, "storage_meta.json")
        self.journal_path = os.path.join(data_dir, "storage_journal.jsonl")
        self.snapshot_names = snapshot_names or {}
        self.data = {ns: {} for ns in self.paths}
        self.meta = {ns: {} for ns in self.paths}   # ns -> {flatkey: [ts, deleted]}
        self.dirty = set()
        self.journal_pos = 0
        self.load_warnings = []
        self.last_snapshot_ts = 0.0
        self.changed_since_snapshot = False
        self.pending_reason = None
        self.last_flush_ok = None
        self.last_flush_error = None
        # 시트
        self.sheets = sheets
        self.sheets_factory = sheets_factory
        self.sheets_enabled = sheets is not None or sheets_factory is not None
        self.sheets_ready = not self.sheets_enabled
        self.sheets_disabled_reason = None
        self.sheet_pending = set()     # (ns, flatkey)
        self.sheet_backoff_until = 0.0
        self.sheet_fail_count = 0
        self.sheet_last_ok = None
        self.sheet_last_error = None
        self._stop = False

        self._load_local()
        if self.sheets_enabled:
            self._try_sheet_sync()
        if start_thread:
            t = threading.Thread(target=self._loop, name="storage-flusher", daemon=True)
            t.start()

    # ------------------------------------------------------------------
    # 시작 시 로컬 불러오기
    # ------------------------------------------------------------------
    def _latest_snapshot_for(self, ns):
        name = self.snapshot_names.get(ns)
        if not name or not os.path.isdir(self.backup_dir):
            return None
        files = sorted([f for f in os.listdir(self.backup_dir) if f.startswith("backup_") and f.endswith(".json")],
                       reverse=True)
        for f in files:
            try:
                b = _read_json_file(os.path.join(self.backup_dir, f))
                if isinstance(b, dict) and isinstance(b.get(name), dict):
                    return b[name], f
            except Exception:
                continue
        return None

    def _load_local(self):
        try:
            m = _read_json_file(self.meta_path) if os.path.exists(self.meta_path) else {}
        except Exception:
            m = {}
        for ns, path in self.paths.items():
            self.meta[ns] = {k: list(v) for k, v in (m.get(ns) or {}).items()}
            if os.path.exists(path):
                try:
                    obj = _read_json_file(path)
                    if not isinstance(obj, dict):
                        raise ValueError("최상위가 dict 가 아님")
                    self.data[ns] = self._normalize(ns, obj)
                    continue
                except Exception as e:
                    bad = f"{path}.corrupt_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    try:
                        os.replace(path, bad)
                    except Exception:
                        pass
                    snap = self._latest_snapshot_for(ns)
                    if snap:
                        self.data[ns] = self._normalize(ns, snap[0])
                        self.load_warnings.append(
                            f"[{os.path.basename(path)}] 파일이 손상되어 읽을 수 없었습니다({e}). "
                            f"손상 파일은 {os.path.basename(bad)} 로 보관하고, 백업 {snap[1]} 에서 불러왔습니다.")
                    else:
                        self.load_warnings.append(
                            f"[{os.path.basename(path)}] 파일이 손상되었고 백업도 없습니다. 손상 파일은 {os.path.basename(bad)} 로 보관했습니다.")
                    self.dirty.add(ns)
            else:
                self.data[ns] = {}
                self.dirty.add(ns)
        self._replay_journal()

    def _normalize(self, ns, obj):
        if self.depth.get(ns) == 2:
            return {k: (dict(v) if isinstance(v, dict) else {}) for k, v in obj.items()}
        return dict(obj)

    def _replay_journal(self):
        if not os.path.exists(self.journal_path):
            return
        n = 0
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue  # 마지막 줄이 끊겼을 수 있음
                ns, k, ts = e.get("ns"), e.get("k"), float(e.get("ts", 0))
                if ns not in self.data or k is None:
                    continue
                cur = self.meta[ns].get(k)
                if cur and cur[0] > ts:
                    continue
                self._apply(ns, k, e.get("v"), bool(e.get("del")), ts, journal=False)
                n += 1
        if n:
            self.load_warnings.append(f"비정상 종료 전 저장 기록 {n}건을 저널에서 복구했습니다.")
        for ns in self.paths:
            self.dirty.add(ns)

    # ------------------------------------------------------------------
    # 키 다루기
    # ------------------------------------------------------------------
    def _get_flat(self, ns, k):
        if self.depth.get(ns) == 2:
            uk, _, act = k.partition(SEP)
            return self.data[ns].get(uk, {}).get(act)
        return self.data[ns].get(k)

    def _iter_flat(self, ns):
        if self.depth.get(ns) == 2:
            for uk, acts in self.data[ns].items():
                for act, v in acts.items():
                    yield f"{uk}{SEP}{act}", v
        else:
            for k, v in self.data[ns].items():
                yield k, v

    def _apply(self, ns, k, value, deleted, ts, journal=True):
        """락 안에서 호출. 기존 중첩 객체를 '제자리 수정'하지 않고 교체(copy-on-write)한다."""
        d = self.data[ns]
        if self.depth.get(ns) == 2:
            uk, _, act = k.partition(SEP)
            user = dict(d.get(uk, {}))
            if deleted:
                user.pop(act, None)
            else:
                user[act] = value
            if user:
                d[uk] = user
            else:
                d.pop(uk, None)
        else:
            if deleted:
                d.pop(k, None)
            else:
                d[k] = value
        self.meta[ns][k] = [ts, bool(deleted)]
        self.dirty.add(ns)
        self.changed_since_snapshot = True
        if self.sheets_enabled:
            self.sheet_pending.add((ns, k))
        if journal:
            entry = {"ns": ns, "k": k, "ts": ts}
            if deleted:
                entry["del"] = True
            else:
                entry["v"] = value
            line = json.dumps(entry, ensure_ascii=False) + "\n"
            with open(self.journal_path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
                self.journal_pos = f.tell()

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------
    def load(self, ns):
        """현재 데이터의 사본(TrackedDict)을 돌려준다. 호출자는 마음대로 수정해도 된다."""
        with self.lock:
            cur = self.data[ns]
            base = dict(cur)  # 얕은 복사: 중첩 객체는 교체 방식이라 이후 바뀌지 않음
            out = TrackedDict(copy.deepcopy(cur))
        out._ns = ns
        out._base = base
        return out

    def get(self, ns, key, sub=None, default=None):
        with self.lock:
            if self.depth.get(ns) == 2:
                v = self.data[ns].get(key, {})
                v = v.get(sub, default) if sub is not None else v
            else:
                v = self.data[ns].get(key, default)
            return copy.deepcopy(v)

    def set(self, ns, key, value, sub=None):
        """단일 항목 저장(가장 안전한 방식)."""
        self._check_ready()
        k = f"{key}{SEP}{sub}" if self.depth.get(ns) == 2 else key
        with self.lock:
            self._apply(ns, k, copy.deepcopy(value), False, _now())

    def delete(self, ns, key, sub=None):
        self._check_ready()
        with self.lock:
            if self.depth.get(ns) == 2 and sub is None:
                for act in list(self.data[ns].get(key, {}).keys()):
                    self._apply(ns, f"{key}{SEP}{act}", None, True, _now())
            else:
                k = f"{key}{SEP}{sub}" if self.depth.get(ns) == 2 else key
                if self._get_flat(ns, k) is not None or k in self.meta[ns]:
                    self._apply(ns, k, None, True, _now())

    def save(self, ns, obj):
        """load() 로 받은 사본을 고쳐서 돌려주면 '바뀐 항목만' 반영한다.
        load() 를 거치지 않은 일반 dict 이면 전체 교체(백업 복원 등)로 처리한다.
        반환: 바뀐 항목 수"""
        self._check_ready()
        if not isinstance(obj, dict):
            raise TypeError("dict 만 저장할 수 있습니다.")
        tracked = isinstance(obj, TrackedDict) and getattr(obj, "_ns", None) == ns
        with self.lock:
            base = obj._base if tracked else dict(self.data[ns])
            changes = []  # (flatkey, value, deleted)
            if self.depth.get(ns) == 2:
                for uk in set(base) | set(obj):
                    bu = base.get(uk) or {}
                    nu = obj.get(uk)
                    if nu is None:
                        if uk in base:
                            # 사용자 전체 삭제 -> 현재 저장소에 있는 그 사용자의 모든 항목 삭제
                            for act in list(self.data[ns].get(uk, {}).keys()):
                                changes.append((f"{uk}{SEP}{act}", None, True))
                        continue
                    if not isinstance(nu, dict):
                        continue
                    for act in set(bu) | set(nu):
                        if act not in nu:
                            changes.append((f"{uk}{SEP}{act}", None, True))
                        elif act not in bu or nu[act] != bu[act]:
                            changes.append((f"{uk}{SEP}{act}", copy.deepcopy(nu[act]), False))
            else:
                for k in set(base) | set(obj):
                    if k not in obj:
                        changes.append((k, None, True))
                    elif k not in base or obj[k] != base[k]:
                        changes.append((k, copy.deepcopy(obj[k]), False))
            ts = _now()
            for k, v, dele in changes:
                self._apply(ns, k, v, dele, ts)
            if tracked:
                # 같은 사본으로 다시 save 해도 중복 반영되지 않도록 기준을 갱신
                obj._base = dict(self.data[ns])
            return len(changes)

    def replace_all(self, ns, obj):
        """백업 복원용: 전체를 obj 로 교체(바뀐 항목만 기록)."""
        plain = dict(obj)
        return self.save(ns, plain)

    def _check_ready(self):
        if not self.sheets_ready:
            raise StorageNotReady("구글 시트 저장소와 아직 동기화되지 않았습니다. 잠시 후 다시 저장해 주세요.")

    # ------------------------------------------------------------------
    # 스냅샷(백업)
    # ------------------------------------------------------------------
    def request_snapshot(self, reason="자동 스냅샷", force=False):
        """force=False 면 마지막 스냅샷 후 5분이 안 지났을 때 바로 만들지 않고 '예약'만 한다."""
        with self.lock:
            self.pending_reason = reason
            due = force or (_now() - self.last_snapshot_ts >= SNAPSHOT_EVERY)
        if due:
            return self.snapshot_now(reason)
        return None

    def snapshot_now(self, reason="수동 스냅샷"):
        with self.lock:
            bundle = {"timestamp": _kst_str(), "reason": reason}
            for ns, name in self.snapshot_names.items():
                bundle[name] = dict(self.data[ns])  # 얕은 복사로 충분(copy-on-write)
            self.last_snapshot_ts = _now()
            self.changed_since_snapshot = False
            self.pending_reason = None
        fname = "backup_" + (datetime.datetime.utcnow() + datetime.timedelta(hours=9)).strftime("%Y%m%d_%H%M%S") + ".json"
        path = os.path.join(self.backup_dir, fname)
        if os.path.exists(path):
            path = path.replace(".json", f"_{int(time.time()*1000) % 1000:03d}.json")
        _atomic_write(path, json.dumps(bundle, ensure_ascii=False))
        files = sorted([f for f in os.listdir(self.backup_dir) if f.startswith("backup_") and f.endswith(".json")])
        for old in files[:-SNAPSHOT_KEEP]:
            try:
                os.remove(os.path.join(self.backup_dir, old))
            except Exception:
                pass
        return os.path.basename(path)

    # ------------------------------------------------------------------
    # 파일 반영
    # ------------------------------------------------------------------
    def flush(self):
        with self.lock:
            if not self.dirty:
                return
            todo = {ns: dict(self.data[ns]) for ns in self.dirty}
            meta = {ns: dict(m) for ns, m in self.meta.items()}
            pos = self.journal_pos
            self.dirty = set()
        try:
            for ns, obj in todo.items():
                indent = None if self.depth.get(ns) == 2 else 2
                _atomic_write(self.paths[ns], json.dumps(obj, ensure_ascii=False, indent=indent))
            _atomic_write(self.meta_path, json.dumps(meta, ensure_ascii=False))
            with self.lock:
                if self.journal_pos == pos and not self.dirty:
                    open(self.journal_path, "w", encoding="utf-8").close()
                    self.journal_pos = 0
            self.last_flush_ok = _now()
            self.last_flush_error = None
        except Exception as e:
            with self.lock:
                self.dirty |= set(todo)
            self.last_flush_error = f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    # 구글 시트 동기화
    # ------------------------------------------------------------------
    def _try_sheet_sync(self):
        """시트의 내용을 받아와 '더 최신인 쪽'으로 병합한다."""
        try:
            if self.sheets is None:
                self.sheets = self.sheets_factory()
            remote = {ns: self.sheets.fetch_all(ns) for ns in self.paths}
        except Exception as e:
            self.sheet_last_error = f"{type(e).__name__}: {e}"
            msg = str(e).lower()
            permanent = any(s in msg for s in ("permission", "403", "404", "not found", "invalid_grant",
                                               "invalid jwt", "private key", "unable to load pem"))
            if permanent or type(e).__name__ in ("SpreadsheetNotFound", "ValueError", "KeyError", "TypeError"):
                # 설정 오류: 시트를 끄고 로컬만으로 동작(관리자 화면에 경고)
                self.sheets_enabled = False
                self.sheets_ready = True
                self.sheets_disabled_reason = self.sheet_last_error
            return False
        with self.lock:
            pulled = pushed = 0
            for ns in self.paths:
                rem = remote.get(ns, {})
                for k, (rts, rdel, rval) in rem.items():
                    loc = self.meta[ns].get(k)
                    lts = loc[0] if loc else 0.0
                    if rts > lts:
                        self._apply(ns, k, rval, rdel, rts, journal=True)
                        self.sheet_pending.discard((ns, k))
                        pulled += 1
                    elif lts > rts:
                        self.sheet_pending.add((ns, k))
                # 로컬에만 있는 항목(처음 연결할 때의 기존 데이터 포함)은 시트로 올림
                for k, _v in list(self._iter_flat(ns)):
                    if k not in rem:
                        if k not in self.meta[ns]:
                            self.meta[ns][k] = [_now(), False]
                            self.dirty.add(ns)
                        self.sheet_pending.add((ns, k))
                        pushed += 1
                for k, (lts, ldel) in list(self.meta[ns].items()):
                    if ldel and k not in rem:
                        self.sheet_pending.add((ns, k))
            self.sheets_ready = True
            if pulled:
                self.load_warnings.append(f"구글 시트에서 최신 자료 {pulled}건을 불러왔습니다.")
        self.sheet_last_ok = _now()
        return True

    def push_to_sheets(self):
        if not (self.sheets_enabled and self.sheets_ready and self.sheets is not None):
            return
        if _now() < self.sheet_backoff_until:
            return
        with self.lock:
            if not self.sheet_pending:
                return
            keys = list(self.sheet_pending)
            self.sheet_pending = set()
            by_ns = {}
            for ns, k in keys:
                m = self.meta[ns].get(k, [_now(), False])
                val = None if m[1] else self._get_flat(ns, k)
                deleted = m[1] or val is None
                by_ns.setdefault(ns, []).append((k, m[0], deleted, val))
        done = set()
        try:
            for ns, items in by_ns.items():
                self.sheets.push(ns, items)
                done |= {(ns, it[0]) for it in items}
            self.sheet_fail_count = 0
            self.sheet_last_ok = _now()
            self.sheet_last_error = None
        except Exception as e:
            with self.lock:
                self.sheet_pending |= (set(keys) - done)
            self.sheet_fail_count += 1
            self.sheet_backoff_until = _now() + min(60, 2 ** self.sheet_fail_count)
            self.sheet_last_error = f"{type(e).__name__}: {e}"

    # ------------------------------------------------------------------
    # 백그라운드 루프
    # ------------------------------------------------------------------
    def _loop(self):
        last_push = 0.0
        last_sync_try = _now()
        while not self._stop:
            time.sleep(FLUSH_EVERY)
            try:
                self.flush()
                if self.changed_since_snapshot and _now() - self.last_snapshot_ts >= SNAPSHOT_EVERY:
                    self.snapshot_now(self.pending_reason or "자동 주기 스냅샷")
                if self.sheets_enabled and not self.sheets_ready and _now() - last_sync_try > 10:
                    last_sync_try = _now()
                    self._try_sheet_sync()
                if _now() - last_push >= SHEET_PUSH_EVERY:
                    last_push = _now()
                    self.push_to_sheets()
            except Exception:
                self.last_flush_error = traceback.format_exc(limit=2)

    def status(self):
        with self.lock:
            return {
                "sheets_enabled": self.sheets_enabled,
                "sheets_ready": self.sheets_ready,
                "sheets_disabled_reason": self.sheets_disabled_reason,
                "sheet_pending": len(self.sheet_pending),
                "sheet_last_ok": _kst_str(self.sheet_last_ok) if self.sheet_last_ok else None,
                "sheet_last_error": self.sheet_last_error,
                "last_flush_ok": _kst_str(self.last_flush_ok) if self.last_flush_ok else None,
                "last_flush_error": self.last_flush_error,
                "warnings": list(self.load_warnings),
            }

    def close(self):
        self._stop = True
        self.flush()
        self.push_to_sheets()


# =====================================================================
# 앱에서 쓰는 싱글턴
# =====================================================================
_STORE = None
_STORE_LOCK = threading.Lock()


def get_store(paths, depth, backup_dir, snapshot_names, sheets_conf=None, data_dir="."):
    """프로세스 전체에서 하나의 저장소만 만든다(Streamlit 재실행 사이에도 유지)."""
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            factory = None
            if sheets_conf:
                info, url = sheets_conf
                factory = lambda: SheetsMirror(info, url)  # noqa: E731
            _STORE = Store(paths, depth, data_dir=data_dir, backup_dir=backup_dir,
                           sheets_factory=factory, snapshot_names=snapshot_names)
        return _STORE
