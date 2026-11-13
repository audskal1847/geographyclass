import streamlit as st
import streamlit.components.v1 as components
import json, os, base64, datetime, threading, io, zipfile
import pandas as pd
_STORAGE_SRC = r'''# -*- coding: utf-8 -*-
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

ENGINE_VERSION = "1.7 (메모리 절약: 큰 파일 나눠 쓰기)"
SEP = "||"                    # 2단계 키 구분자 (학생키||수행평가명)
CHUNK = 40000                 # 구글 시트 셀 1칸 최대 5만 자 -> 4만 자씩 분할
MAX_REQ_CHARS = 1_500_000     # 시트 API 요청 1회당 최대 전송량(여유 있게)
SNAPSHOT_EVERY = 900          # 변경이 있으면 최대 15분마다 전체 스냅샷
SNAPSHOT_KEEP = 24            # 스냅샷 보관 개수
FLUSH_EVERY = 10.0            # 전체 파일 반영 주기(초) — 저장 즉시 기록(저널)이 있어 자주 쓸 필요 없음, CPU 절약
SHEET_PUSH_EVERY = 5.0        # 구글 시트 반영 주기(초)
REPORT_EVERY = 120.0          # 선생님용 응답 보기 탭 갱신 주기(초) — CPU 절약


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


def _atomic_dump(path, obj, indent=None):
    """큰 자료도 메모리에 한꺼번에 문자열로 만들지 않고 조금씩 파일에 씀 (메모리 절약)"""
    tmp = f"{path}.tmp{os.getpid()}_{threading.get_ident()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _slim_media(v, limit=700_000):
    """스냅샷용: 아주 큰 첨부(영상 등)는 표시만 남김"""
    if isinstance(v, str):
        if len(v) > limit and v.startswith("data:"):
            return f"__MEDIA_OMITTED__:{v[5:v.find(';')] if ';' in v[:80] else 'media'}:{len(v)}"
        return v
    if isinstance(v, dict):
        return {k: _slim_media(x, limit) for k, x in v.items()}
    if isinstance(v, list):
        return [_slim_media(x, limit) for x in v]
    return v


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
        # 🛟 사람이 시트를 정렬하거나 줄을 지웠을 수 있으므로, 쓰기 직전에 A열(키)로 줄 위치를 다시 확인
        col_a = ws.col_values(1)
        rowmap = {}
        for i, k in enumerate(col_a[1:], start=2):
            if k:
                rowmap[k] = i
        self.rowmap[ns] = rowmap
        self.next_row[ns] = max(len(col_a) + 1, 2)
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
        try:
            for b in batches:
                ws.batch_update(b, value_input_option="RAW")
        except Exception:
            try:
                fresh = self.sh.worksheet(ns)
                self.ws[ns] = fresh
                self.rows[ns] = fresh.row_count
                self.cols[ns] = fresh.col_count
            except Exception:
                pass
            raise

    VIEW_TITLE = "학생자료_보기"
    VIEW_HEADER = ["학년", "반", "학번", "이름", "수행평가", "마지막 저장(한국시간)"]
    VIEW_FORMULA = (
        '=IFERROR(ARRAYFORMULA(LET('
        'ok, REGEXMATCH(data!A2:A&"", "^\\d학년")*(data!C2:C<>1), '
        'k, FILTER(data!A2:A, ok), '
        't, FILTER(data!E2:E, ok), '
        'uk, REGEXEXTRACT(k, "^(.+)\\|\\|"), '
        'nm, IFERROR(REGEXEXTRACT(VLOOKUP(uk, users!A:F, 6, FALSE), """name"": ""([^""]*)"""), ""), '
        'SORT(HSTACK('
        'REGEXEXTRACT(k, "^(\\d학년)"), '
        'REGEXEXTRACT(k, "^[^_]+_([^_]+)_"), '
        'REGEXEXTRACT(k, "_([^_]+)\\|\\|"), '
        'nm, '
        'REGEXREPLACE(REGEXEXTRACT(k, "\\|\\|(.+)$"), "^\\[\\d학년\\]\\s*", ""), '
        't), 1, TRUE, 2, TRUE, 3, TRUE, 5, TRUE))), "아직 저장된 자료가 없습니다")'
    )

    def write_table(self, title, rows, freeze_cols=3, bold_rows=None, groups=None, group_cols=6):
        """선생님이 읽는 표(응답 보기 탭)를 통째로 다시 쓴다. 내용이 같으면 건너뜀."""
        import hashlib
        if not hasattr(self, "_tables"):
            self._tables = {}
        h = hashlib.md5(json.dumps([rows, bold_rows or [], groups or []], ensure_ascii=False).encode("utf-8")).hexdigest()
        prev = self._tables.get(title)
        if prev and prev["hash"] == h:
            return False
        width = max(len(r) for r in rows) if rows else 1
        try:
            ws = self.sh.worksheet(title)
            created = False
        except Exception:
            ws = self.sh.add_worksheet(title=title, rows=max(len(rows) + 20, 100), cols=max(width, 10))
            created = True
        if prev is None and not created:
            ws.clear()  # 이번 실행에서 처음 쓰는 탭: 예전 내용 정리
            prev = {"n": 0, "w": 0}
        prev = prev or {"n": 0, "w": 0}
        n_rows, n_cols = max(len(rows), prev["n"]), max(width, prev["w"])
        if n_rows > ws.row_count:
            ws.add_rows(n_rows - ws.row_count + 20)
        if n_cols > ws.col_count:
            ws.add_cols(n_cols - ws.col_count + 2)
        padded = [list(r) + [""] * (n_cols - len(r)) for r in rows]
        padded += [[""] * n_cols for _ in range(n_rows - len(rows))]   # 줄어든 만큼 지움
        # 큰 표는 요청 1회 크기 제한을 넘지 않도록 행 묶음으로 나누어 보냄
        start, block, size = 0, [], 0
        for r_i, row in enumerate(padded):
            rs = sum(len(str(x)) for x in row) + 10
            if block and size + rs > MAX_REQ_CHARS:
                ws.update(range_name=f"A{start + 1}", values=block, value_input_option="RAW")
                start, block, size = r_i, [], 0
            block.append(row); size += rs
        if block:
            ws.update(range_name=f"A{start + 1}", values=block, value_input_option="RAW")
        if created or title not in getattr(self, "_formatted", set()):
            if not hasattr(self, "_formatted"):
                self._formatted = set()
            self._formatted.add(title)
            try:
                ws.freeze(rows=1, cols=freeze_cols)
                sid = ws.id
                widths = [90, 70, 70, 70, 120, 110, 200, 220] + [360] * max(1, width - 8)
                reqs = [{"updateDimensionProperties": {"range": {"sheetId": sid, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
                                                       "properties": {"pixelSize": w}, "fields": "pixelSize"}} for i, w in enumerate(widths[:width])]
                reqs.append({"repeatCell": {"range": {"sheetId": sid, "startColumnIndex": 0, "endColumnIndex": width},
                                            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}},
                                            "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}})
                reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 0, "endRowIndex": 1},
                                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.89, "green": 0.95, "blue": 0.99}}},
                                            "fields": "userEnteredFormat(textFormat,backgroundColor)"}})
                self.sh.batch_update({"requests": reqs})
            except Exception:
                pass
        # 표 머리글 줄은 굵게/연한 배경 (예전 줄 서식은 먼저 지움)
        try:
            sid = ws.id
            reqs = [{"repeatCell": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": max(n_rows, 2)},
                                    "cell": {"userEnteredFormat": {"textFormat": {"bold": False}, "backgroundColor": {"red": 1, "green": 1, "blue": 1}}},
                                    "fields": "userEnteredFormat(textFormat,backgroundColor)"}}]
            # 학생별 묶음: 같은 학생의 반·학번·이름 등은 첫 줄에만 보이게(값은 남겨 두어 필터는 그대로 동작) + 학생 사이 굵은 구분선
            reqs.append({"updateBorders": {"range": {"sheetId": sid, "startRowIndex": 1, "endRowIndex": max(n_rows, 2), "startColumnIndex": 0, "endColumnIndex": max(width, 1)},
                                           "top": {"style": "NONE"}, "innerHorizontal": {"style": "NONE"}}})
            for first, last in (groups or []):
                if last > first:
                    reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": first, "endRowIndex": last, "startColumnIndex": 0, "endColumnIndex": group_cols},
                                                "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}}}},
                                                "fields": "userEnteredFormat.textFormat.foregroundColor"}})
                if first > 2:
                    reqs.append({"updateBorders": {"range": {"sheetId": sid, "startRowIndex": first - 1, "endRowIndex": first, "startColumnIndex": 0, "endColumnIndex": max(width, 1)},
                                                   "top": {"style": "SOLID_MEDIUM", "color": {"red": 0.35, "green": 0.45, "blue": 0.6}}}})
            for r in (bold_rows or []):
                reqs.append({"repeatCell": {"range": {"sheetId": sid, "startRowIndex": r - 1, "endRowIndex": r, "startColumnIndex": 6},
                                            "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95}}},
                                            "fields": "userEnteredFormat(textFormat,backgroundColor)"}})
            for k in range(0, len(reqs), 500):
                self.sh.batch_update({"requests": reqs[k:k + 500]})
        except Exception:
            pass
        self._tables[title] = {"hash": h, "n": len(rows), "w": width}
        return True

    def ensure_view(self):
        """선생님이 학년 → 반 → 수행평가로 걸러 볼 수 있는 읽기 전용 보기 탭(없을 때만 생성)."""
        try:
            self.sh.worksheet(self.VIEW_TITLE)
            return False
        except Exception:
            pass
        ws = self.sh.add_worksheet(title=self.VIEW_TITLE, rows=1000, cols=6)
        ws.update(range_name="A1:F1", values=[self.VIEW_HEADER], value_input_option="RAW")
        ws.update(range_name="A2", values=[[self.VIEW_FORMULA]], value_input_option="USER_ENTERED")
        try:
            ws.freeze(rows=1)
        except Exception:
            pass
        return True


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
        self.report_hook = None        # 앱이 등록: 선생님용 응답 보기 탭 생성 함수
        self.report_dirty = True
        self.last_report = 0.0
        self.report_last_error = None
        self.report_last_ok = None

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
        if ns != "drafts":
            self.report_dirty = True
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
        _atomic_dump(path, _slim_media(bundle))
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
                _atomic_dump(self.paths[ns], obj, indent=indent)
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
            if permanent or type(e).__name__ in ("SpreadsheetNotFound", "ValueError", "KeyError", "TypeError",
                                                "ModuleNotFoundError", "ImportError"):
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
        try:
            if hasattr(self.sheets, "ensure_view"):
                self.sheets.ensure_view()
        except Exception:
            pass
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
                self.run_report()
            except Exception:
                self.last_flush_error = traceback.format_exc(limit=2)

    def run_report(self, force=False):
        if not (self.report_hook and self.sheets_enabled and self.sheets_ready and self.sheets is not None):
            return
        if not force and (not self.report_dirty or _now() - self.last_report < REPORT_EVERY):
            return
        self.report_dirty = False
        self.last_report = _now()
        try:
            self.report_hook(self)
            self.report_last_error = None
            self.report_last_ok = _now()
        except Exception as e:
            self.report_dirty = True
            self.report_last_error = f"{type(e).__name__}: {e}"

    def meta_ts(self, ns, key):
        with self.lock:
            m = self.meta.get(ns, {}).get(key)
            return m[0] if m else None

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
                "report_last_error": self.report_last_error,
                "report_last_ok": _kst_str(self.report_last_ok) if self.report_last_ok else None,
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
'''
# =====================================================================
# 💾 안전 저장 엔진 내장 (storage.py 를 따로 올리지 않아도 됩니다)
# =====================================================================
import sys as _sys, types as _types
try:
    import storage  # 같은 폴더에 storage.py 가 있으면 그것을 사용
except ModuleNotFoundError:
    if "_embedded_storage" not in _sys.modules:
        _m = _types.ModuleType("_embedded_storage")
        exec(compile(_STORAGE_SRC, "embedded_storage.py", "exec"), _m.__dict__)
        _sys.modules["_embedded_storage"] = _m
    storage = _sys.modules["_embedded_storage"]
from PIL import Image

def process_sketch_image(uploaded_file, max_width=800):
    """스마트폰 고화질 사진을 경량화하여 JSON에 안전하게 저장 가능한 Base64로 변환"""
    if uploaded_file is None:
        return ""
    try:
        img = Image.open(uploaded_file)
        try:
            from PIL import ImageOps as _IO
            img = _IO.exif_transpose(img)   # 📱 휴대폰 사진이 옆으로 누워 보이던 문제 해결
        except Exception:
            pass
        if img.mode in ("RGBA", "P", "LA", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if w > max_width:
            new_h = int(h * (max_width / w))
            img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        b64_data = base64.b64encode(buf.getvalue()).decode("utf-8")
        return f"data:image/jpeg;base64,{b64_data}"
    except Exception:
        return ""
def create_storyboard_gif(images_b64_list, duration=1500):
    """업로드된 4컷 이미지를 순서대로 이어붙여 1.5초 간격의 슬라이드 영상(GIF)으로 자동 변환"""
    pil_imgs = []
    target_size = (700, 420)
    for b64 in images_b64_list:
        if not b64 or not b64.startswith("data:image"):
            continue
        try:
            header, data = b64.split(",", 1) if "," in b64 else ("", b64)
            img = Image.open(io.BytesIO(base64.b64decode(data)))
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            # 비율 유지 리사이즈 & 흰색 캔버스 중앙 배치
            img.thumbnail(target_size, Image.Resampling.LANCZOS)
            bg = Image.new("RGB", target_size, (255, 255, 255))
            offset = ((target_size[0] - img.size[0]) // 2, (target_size[1] - img.size[1]) // 2)
            bg.paste(img, offset)
            pil_imgs.append(bg)
        except Exception:
            pass
    if not pil_imgs:
        return ""
    buf = io.BytesIO()
    # 4컷 순환 애니메이션 GIF 생성
    pil_imgs[0].save(buf, format="GIF", save_all=True, append_images=pil_imgs[1:], duration=duration, loop=0)
    b64_gif = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/gif;base64,{b64_gif}"

def take_upload(label, key, processor, **kw):
    """파일을 올리면 바로 가볍게 변환해 보관하고, 업로드 칸은 다음 화면부터 비워서 원본 파일이 서버 메모리에 남지 않게 함.
    (휴대폰 원본 사진·영상이 학생마다 메모리에 계속 남아 있으면 수업 중 앱이 멈춤)  반환: 변환된 값 또는 None"""
    scope = st.session_state.get("_render_now", "")
    nkey, vkey = f"{key}::n", f"{key}::val::{scope}"
    n = st.session_state.get(nkey, 0)
    up = st.file_uploader(label, key=f"{key}::{n}", **kw)
    if up is not None:
        try:
            val = processor(up)
        except Exception:
            val = None
        if val:
            st.session_state[vkey] = val
        st.session_state[nkey] = n + 1              # 다음 화면에서는 새 업로드 칸 → 원본 파일은 메모리에서 지워짐(실패한 파일도)
    if st.session_state.get(vkey):
        st.caption("✅ 올린 파일이 반영되었습니다. 하단 [저장하기]를 눌러야 최종 저장됩니다.")
    return st.session_state.get(vkey)

def process_uploaded_video(uploaded_file, max_mb=10):
    """직접 제작한 시뮬레이션 영상(MP4/GIF)을 Base64로 안전하게 변환 (용량 초과 방지)"""
    if uploaded_file is None:
        return ""
    if uploaded_file.size > max_mb * 1024 * 1024:
        st.error(f"⚠️ 영상 파일 용량이 {max_mb}MB를 초과했습니다. 더 짧거나 작은 파일로 올려주세요.")
        return ""
    fid = f"_vidref::{getattr(uploaded_file, 'file_id', '')}::{uploaded_file.name}::{uploaded_file.size}"
    if fid in st.session_state:
        return st.session_state[fid]               # 같은 파일을 화면 갱신 때마다 다시 처리하지 않음
    bytes_data = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
    mime = uploaded_file.type or "video/mp4"
    ref = media_save_bytes(bytes_data, _MIME_EXT.get(mime, (uploaded_file.name.rsplit(".", 1)[-1] if "." in uploaded_file.name else "mp4").lower()))
    st.session_state[fid] = ref
    return ref

# ===================== 🎨 미디어 파사드 이미지 도구 =====================
from PIL import ImageOps, ImageChops, ImageEnhance, ImageFilter, ImageDraw

def _b64_to_img(b64):
    if not b64 or not isinstance(b64, str) or not b64.startswith("data:image"):
        return None
    try:
        data = b64.split(",", 1)[1]
        img = Image.open(io.BytesIO(base64.b64decode(data)))
        img = ImageOps.exif_transpose(img)
        return img.convert("RGB")
    except Exception:
        return None

def _img_to_b64(img, fmt="JPEG", quality=82):
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=quality)
    mime = "jpeg" if fmt == "JPEG" else fmt.lower()
    return f"data:image/{mime};base64," + base64.b64encode(buf.getvalue()).decode("utf-8")

MF_DEFAULT_FACADE = {"l": 20, "t": 15, "w": 60, "h": 60, "dark": 65, "power": 85, "fit": "꽉 채우기",
                     "mode": "사각형", "q": None}

def mf_rect_to_quad(s):
    """반듯한 사각형 설정 → 네 모서리(왼쪽 위, 오른쪽 위, 오른쪽 아래, 왼쪽 아래, % 단위)"""
    l, t, w, h = s["l"], s["t"], s["w"], s["h"]
    return [l, t, min(100, l + w), t, min(100, l + w), min(100, t + h), l, min(100, t + h)]

def _perspective_coeffs(dst, src):
    """PIL 원근 변환 계수: 결과 이미지의 점(dst) → 원본 스케치의 점(src)"""
    import numpy as np
    A, B = [], []
    for (x, y), (u, v) in zip(dst, src):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); B.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); B.append(v)
    return np.linalg.solve(np.array(A, dtype=float), np.array(B, dtype=float)).tolist()

def make_facade_composite(building_b64, sketch_b64, s=None, max_w=900):
    """건물 사진을 밤처럼 어둡게 만들고, 지정한 벽면 영역에 스케치를 '빛'처럼 투사(스크린 합성 + 번짐)"""
    s = dict(MF_DEFAULT_FACADE, **(s or {}))
    base = _b64_to_img(building_b64)
    sk = _b64_to_img(sketch_b64)
    if base is None:
        return ""
    if base.width > max_w:
        base = base.resize((max_w, int(base.height * max_w / base.width)), Image.Resampling.LANCZOS)
    W, H = base.size
    # 1) 야간 분위기: 밝기 낮추고 푸른 밤빛
    dark = max(0, min(95, int(s["dark"]))) / 100
    night = ImageEnhance.Brightness(base).enhance(1 - dark * 0.85)
    night = Image.blend(night, Image.new("RGB", (W, H), (8, 14, 38)), dark * 0.35)
    if sk is None:
        return _img_to_b64(night)
    power = max(10, min(100, int(s["power"]))) / 100
    mask_pts = s.get("mask") if isinstance(s.get("mask"), (list, tuple)) and len(s.get("mask") or []) >= 3 else None
    if mask_pts and not (s.get("mode") == "네 모서리" and s.get("q")):
        s = dict(s, mode="네 모서리", q=mf_rect_to_quad(s))      # 사각형 방식이어도 같은 방법으로 처리
    if s.get("mode") == "네 모서리" and s.get("q") and len(s["q"]) == 8:
        # 🧭 기울어진 벽: 스케치를 네 모서리에 맞게 원근 변형
        q = [max(0, min(100, float(v))) for v in s["q"]]
        pts = [(q[0] * W / 100, q[1] * H / 100), (q[2] * W / 100, q[3] * H / 100),
               (q[4] * W / 100, q[5] * H / 100), (q[6] * W / 100, q[7] * H / 100)]
        sk2 = ImageEnhance.Color(sk).enhance(1.35)
        sw, sh = sk2.size
        try:
            coeffs = _perspective_coeffs(pts, [(0, 0), (sw, 0), (sw, sh), (0, sh)])
            warped = sk2.transform((W, H), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC, fillcolor=(0, 0, 0))
        except Exception:
            return _img_to_b64(night)
        mask = Image.new("L", (W, H), 0)
        if mask_pts:   # ✂️ 건물 모양대로 따내기(ㄴ자·ㄱ자 등 사각형이 아닌 벽면)
            ImageDraw.Draw(mask).polygon([(float(x) * W / 100, float(y) * H / 100) for x, y in mask_pts], fill=255)
        else:
            ImageDraw.Draw(mask).polygon(pts, fill=255)
        span = max(abs(pts[1][0] - pts[0][0]), abs(pts[2][0] - pts[3][0]), 40)
        glow = Image.composite(warped, Image.new("RGB", (W, H)), mask).filter(ImageFilter.GaussianBlur(max(6, int(span) // 25)))
        out = Image.blend(night, ImageChops.screen(night, glow), 0.45 * power)
        lit = ImageChops.screen(ImageEnhance.Brightness(out).enhance(0.22), warped)
        lit = Image.blend(lit, warped, 0.35)
        out = Image.composite(Image.blend(out, lit, power), out, mask)
        return _img_to_b64(out)
    # 2) 투사 영역(%) → 픽셀
    l = int(W * max(0, min(95, s["l"])) / 100); t = int(H * max(0, min(95, s["t"])) / 100)
    w = max(10, int(W * max(5, min(100, s["w"])) / 100)); h = max(10, int(H * max(5, min(100, s["h"])) / 100))
    w = min(w, W - l); h = min(h, H - t)
    if s.get("fit") == "비율 유지":
        sk2 = ImageOps.contain(sk, (w, h), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (w, h), (0, 0, 0))
        canvas.paste(sk2, ((w - sk2.width) // 2, (h - sk2.height) // 2))
        sk2 = canvas
    else:
        sk2 = ImageOps.fit(sk, (w, h), Image.Resampling.LANCZOS)
    sk2 = ImageEnhance.Color(sk2).enhance(1.35)
    # 3) 빛 번짐(글로우): 흐리게 만든 빛을 넓게 스크린 합성
    glow_layer = Image.new("RGB", (W, H), (0, 0, 0))
    glow_layer.paste(sk2, (l, t))
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(max(6, w // 25)))
    out = Image.blend(night, ImageChops.screen(night, glow_layer), 0.45 * power)
    # 4) 벽면에 선명한 빛 투사: 밝은 낮 사진이어도 영상이 또렷하도록, 투사 면을 먼저 어둡게(스크린처럼) 한 뒤 빛을 더함
    region = out.crop((l, t, l + w, t + h))
    screen_wall = ImageEnhance.Brightness(region).enhance(0.22)          # 건물 질감은 살짝 남김
    lit = ImageChops.screen(screen_wall, sk2)
    lit = Image.blend(lit, sk2, 0.35)                                     # 빛의 색을 더 선명하게
    out.paste(Image.blend(region, lit, power), (l, t))
    return _img_to_b64(out)

def make_facade_gif(frames_b64, seconds, width=640, fade_steps=4):
    """컷 이미지들을 각 컷의 소요 시간에 맞춰 연결하고, 컷 사이를 부드럽게 전환(크로스페이드)"""
    imgs = [(_b64_to_img(b), sec) for b, sec in zip(frames_b64, seconds)]
    imgs = [(im, sec) for im, sec in imgs if im is not None]
    if not imgs:
        return ""
    H = int(imgs[0][0].height * width / imgs[0][0].width)
    H = max(200, min(H, 520))
    frames, durs = [], []
    norm = [ImageOps.pad(im, (width, H), color=(0, 0, 0)) for im, _ in imgs]
    for i, (im, sec) in enumerate(zip(norm, [s for _, s in imgs])):
        frames.append(im); durs.append(int(max(1.0, min(5.0, float(sec or 3))) * 1000))   # 미리보기는 컷당 1~5초
        nxt = norm[(i + 1) % len(norm)]
        if len(norm) > 1:
            for k in range(1, fade_steps + 1):
                frames.append(Image.blend(im, nxt, k / (fade_steps + 1))); durs.append(90)
    pal = [f.convert("P", palette=Image.Palette.ADAPTIVE, colors=200) for f in frames]
    buf = io.BytesIO()
    pal[0].save(buf, format="GIF", save_all=True, append_images=pal[1:], duration=durs, loop=0, optimize=True)
    return "data:image/gif;base64," + base64.b64encode(buf.getvalue()).decode("utf-8")


def mf_draw_guide(img_b64, s):
    """미리보기 위에 빛 비출 영역의 테두리와 모서리 번호(1~4)를 표시"""
    im = _b64_to_img(img_b64)
    if im is None:
        return img_b64
    W, H = im.size
    q = s["q"] if s.get("mode") == "네 모서리" and s.get("q") else mf_rect_to_quad(s)
    pts = [(q[i] * W / 100, q[i + 1] * H / 100) for i in range(0, 8, 2)]
    d = ImageDraw.Draw(im)
    d.line(pts + [pts[0]], fill=(255, 230, 0), width=max(2, W // 300))
    r = max(9, W // 60)
    try:
        from PIL import ImageFont
        font = ImageFont.load_default(size=int(r * 1.4))
    except Exception:
        font = None
    if isinstance(s.get("mask"), (list, tuple)) and len(s["mask"]) >= 3:
        mp = [(float(x) * W / 100, float(y) * H / 100) for x, y in s["mask"]]
        d.line(mp + [mp[0]], fill=(0, 200, 255), width=max(2, W // 300))
    for n, (x, y) in enumerate(pts, start=1):
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 60, 60), outline=(255, 255, 255), width=2)
        d.text((x, y), str(n), fill=(255, 255, 255), anchor="mm", font=font)
    return _img_to_b64(im)


@st.cache_data(show_spinner=False, max_entries=30)
def mf_cached_composite(site, sketch, facade_items):
    return make_facade_composite(site, sketch, json.loads(facade_items) if isinstance(facade_items, str) else dict(facade_items))

_MF_EDITOR_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; background: transparent; font-family: "Noto Sans KR", "Malgun Gothic", sans-serif; }
  #wrap { position: relative; user-select: none; -webkit-user-select: none; touch-action: none; }
  #img { width: 100%; display: block; border-radius: 10px; }
  #svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  #poly { fill: rgba(255, 230, 0, 0.12); stroke: #ffe600; stroke-width: 2.5; vector-effect: non-scaling-stroke; cursor: move; }
  .h { position: absolute; width: 30px; height: 30px; margin: -15px 0 0 -15px; border-radius: 50%;
       background: #ff3b3b; border: 3px solid #fff; box-shadow: 0 2px 6px rgba(0,0,0,.5);
       color: #fff; font-weight: 800; font-size: 14px; display: flex; align-items: center; justify-content: center; cursor: grab; }
  .h.active { transform: scale(1.25); cursor: grabbing; background: #ff7a00; }
  #mask { fill: rgba(0, 200, 255, 0.18); stroke: #00c8ff; stroke-width: 3; vector-effect: non-scaling-stroke; cursor: move; }
  #poly.frame { fill: none; stroke-dasharray: 6 4; pointer-events: none; }
  .m { position: absolute; width: 18px; height: 18px; margin: -9px 0 0 -9px; border-radius: 50%; background: #0091ea;
       border: 2px solid #fff; box-shadow: 0 1px 4px rgba(0,0,0,.5); cursor: grab; z-index: 3; }
  .m.active { transform: scale(1.3); background: #00b0ff; }
  .mid { position: absolute; width: 18px; height: 18px; margin: -9px 0 0 -9px; border-radius: 50%; background: rgba(255,255,255,.9);
         color: #0091ea; font-weight: 900; font-size: 15px; line-height: 16px; text-align: center; cursor: copy; z-index: 2; border: 1px solid #0091ea; }
  .h { z-index: 4; }
  #hint { font-size: 13px; color: #475569; margin: 8px 2px 2px; line-height: 1.5; }
  #badge { position: absolute; top: 8px; left: 8px; background: rgba(0,0,0,.6); color: #fff; font-size: 12px; padding: 3px 8px; border-radius: 6px; display: none; }
</style></head>
<body>
<div id="wrap">
  <img id="img" alt="">
  <svg id="svg" viewBox="0 0 100 100" preserveAspectRatio="none"><polygon id="poly" points=""></polygon><polygon id="mask" points="" style="display:none"></polygon></svg>
  <div id="mlayer"></div>
  <div class="h" data-i="0">1</div><div class="h" data-i="1">2</div>
  <div class="h" data-i="2">3</div><div class="h" data-i="3">4</div>
  <div id="badge"></div>
</div>
<div id="hint"></div>
<script>
(function () {
  var q = [20, 15, 80, 15, 80, 75, 20, 75];
  var mask = null, editMask = false, lastTap = {};
  var drag = null, disabled = false;
  var maskEl = document.getElementById("mask"), mlayer = document.getElementById("mlayer"), hint = document.getElementById("hint");
  var HINT_Q = "🖱️ <b>빨간 점 ①~④</b>를 끌어 실제 벽면의 네 모서리에 맞추세요. <b>노란 테두리 안쪽</b>을 끌면 영역 전체가 움직입니다. 손을 떼면 합성 화면이 새로 그려지고 왼쪽 슬라이더 값도 함께 바뀝니다.";
  var HINT_M = "✂️ <b>파란 선</b>을 건물 모양에 맞추세요. <b>파란 점</b>은 끌어서 옮기고, 변 가운데 <b>＋</b>를 끌면 꼭짓점이 늘어납니다. 파란 점을 <b>더블클릭</b>하면 지워집니다. 파란 선 안쪽을 끌면 모양 전체가 움직입니다. (빨간 점 ①~④는 원근을 맞추는 틀입니다)";
  var wrap = document.getElementById("wrap"), img = document.getElementById("img");
  var poly = document.getElementById("poly"), badge = document.getElementById("badge");
  var handles = Array.prototype.slice.call(document.querySelectorAll(".h"));

  function send(type, data) {
    var msg = { isStreamlitMessage: true, type: type };
    for (var k in (data || {})) msg[k] = data[k];
    window.parent.postMessage(msg, "*");
  }
  function setHeight() { send("streamlit:setFrameHeight", { height: document.body.scrollHeight + 4 }); }
  function clamp(v) { return Math.max(0, Math.min(100, v)); }
  function draw() {
    poly.setAttribute("points", [0, 2, 4, 6].map(function (i) { return q[i] + "," + q[i + 1]; }).join(" "));
    handles.forEach(function (h, k) { h.style.left = q[2 * k] + "%"; h.style.top = q[2 * k + 1] + "%"; });
    var on = editMask && mask && mask.length >= 3;
    poly.classList.toggle("frame", !!on);
    maskEl.style.display = on ? "" : "none";
    hint.innerHTML = on ? HINT_M : HINT_Q;
    if (!on) { mlayer.innerHTML = ""; return; }
    maskEl.setAttribute("points", mask.map(function (p) { return p[0] + "," + p[1]; }).join(" "));
    if (!drag || drag.type !== "mv") buildMaskHandles(); else positionMaskHandles();
  }
  function buildMaskHandles() {
    mlayer.innerHTML = "";
    mask.forEach(function (p, i) {
      var m = document.createElement("div"); m.className = "m"; m.dataset.i = i; mlayer.appendChild(m);
      m.addEventListener("pointerdown", function (e) {
        if (disabled) return; e.preventDefault(); e.stopPropagation();
        var now = Date.now();
        if (lastTap.i === i && now - lastTap.t < 450) {          // 같은 점을 빠르게 두 번 → 지우기(터치 두 번 누르기도 동일)
          lastTap = {};
          if (mask.length > 3) { mask.splice(i, 1); drag = null; draw(); sendValue(); }
          return;
        }
        lastTap = { i: i, t: now };
        drag = { type: "mv", i: i, moved: false }; m.classList.add("active");
      });
      var a = mask[i], b = mask[(i + 1) % mask.length];
      var mid = document.createElement("div"); mid.className = "mid"; mid.textContent = "+"; mlayer.appendChild(mid);
      mid.style.left = (a[0] + b[0]) / 2 + "%"; mid.style.top = (a[1] + b[1]) / 2 + "%";
      mid.addEventListener("pointerdown", function (e) {
        if (disabled) return; e.preventDefault(); e.stopPropagation();
        mask.splice(i + 1, 0, [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]);
        drag = { type: "mv", i: i + 1 }; buildMaskHandles();
      });
    });
    positionMaskHandles();
  }
  function positionMaskHandles() {
    var ms = mlayer.querySelectorAll(".m");
    for (var k = 0; k < ms.length; k++) { var p = mask[+ms[k].dataset.i]; if (p) { ms[k].style.left = p[0] + "%"; ms[k].style.top = p[1] + "%"; } }
  }
  function sendValue() {
    var r1 = function (v) { return Math.round(v * 10) / 10; };
    send("streamlit:setComponentValue", { value: { q: q.map(r1), mask: (editMask && mask) ? mask.map(function (p) { return [r1(p[0]), r1(p[1])]; }) : null, nonce: Date.now() }, dataType: "json" });
  }
  maskEl.addEventListener("pointerdown", function (e) {
    if (disabled) return; e.preventDefault();
    var p = pos(e); drag = { type: "mmove", sx: p.x, sy: p.y, m0: mask.map(function (v) { return v.slice(); }) };
  });
  function pos(e) {
    var r = wrap.getBoundingClientRect();
    return { x: clamp((e.clientX - r.left) / r.width * 100), y: clamp((e.clientY - r.top) / r.height * 100) };
  }
  handles.forEach(function (h) {
    h.addEventListener("pointerdown", function (e) {
      if (disabled) return;
      e.preventDefault(); e.stopPropagation();
      drag = { type: "corner", i: +h.dataset.i }; h.classList.add("active");
    });
  });
  poly.addEventListener("pointerdown", function (e) {
    if (disabled) return;
    e.preventDefault();
    var p = pos(e); drag = { type: "move", sx: p.x, sy: p.y, q0: q.slice() };
  });
  window.addEventListener("pointermove", function (e) {
    if (!drag) return;
    var p = pos(e);
    if (drag.type === "corner") { q[2 * drag.i] = p.x; q[2 * drag.i + 1] = p.y; }
    else if (drag.type === "mv") { mask[drag.i] = [p.x, p.y]; }
    else if (drag.type === "mmove") {
      var ddx = p.x - drag.sx, ddy = p.y - drag.sy;
      var mx = drag.m0.map(function (v) { return v[0]; }), my = drag.m0.map(function (v) { return v[1]; });
      ddx = Math.max(-Math.min.apply(null, mx), Math.min(100 - Math.max.apply(null, mx), ddx));
      ddy = Math.max(-Math.min.apply(null, my), Math.min(100 - Math.max.apply(null, my), ddy));
      mask = drag.m0.map(function (v) { return [v[0] + ddx, v[1] + ddy]; });
    }
    else {
      var dx = p.x - drag.sx, dy = p.y - drag.sy, xs = [], ys = [];
      for (var i = 0; i < 8; i += 2) { xs.push(drag.q0[i]); ys.push(drag.q0[i + 1]); }
      dx = Math.max(-Math.min.apply(null, xs), Math.min(100 - Math.max.apply(null, xs), dx));
      dy = Math.max(-Math.min.apply(null, ys), Math.min(100 - Math.max.apply(null, ys), dy));
      for (var j = 0; j < 8; j += 2) { q[j] = drag.q0[j] + dx; q[j + 1] = drag.q0[j + 1] + dy; }
    }
    badge.style.display = "block"; badge.textContent = "놓으면 합성 화면이 다시 그려집니다";
    draw();
  });
  function finish() {
    if (!drag) return;
    drag = null; badge.style.display = "none";
    handles.forEach(function (h) { h.classList.remove("active"); });
    sendValue(); draw();
  }
  window.addEventListener("pointerup", finish);
  window.addEventListener("pointercancel", finish);

  window.addEventListener("message", function (e) {
    var d = e.data || {};
    if (d.type !== "streamlit:render") return;
    var a = d.args || {};
    disabled = !!a.disabled || !!d.disabled;
    wrap.style.opacity = disabled ? 0.6 : 1;
    if (!drag && a.q && a.q.length === 8) q = a.q.map(Number);
    editMask = !!a.edit_mask;
    if (!drag) mask = (a.mask && a.mask.length >= 3) ? a.mask.map(function (p) { return [Number(p[0]), Number(p[1])]; }) : null;
    if (a.image && img.getAttribute("src") !== a.image) img.setAttribute("src", a.image);
    draw(); setHeight();
  });
  img.addEventListener("load", function () { draw(); setHeight(); });
  window.addEventListener("resize", setHeight);
  send("streamlit:componentReady", { apiVersion: 1 });
})();
</script>
</body></html>
'''

def mf_corner_editor(image, q, disabled=False, key="mf_drag_editor", mask=None, edit_mask=False):
    """🖱️ 사진 위에서 네 모서리를 마우스로 끌어 맞추는 부품. 손을 떼면 {'q': [...8개], 'nonce': 시각} 을 돌려줌"""
    import tempfile, hashlib, sys as _s
    reg = _s.modules.setdefault("_mf_editor_registry", type(_s)("_mf_editor_registry"))
    if not hasattr(reg, "func"):
        d = os.path.join(tempfile.gettempdir(), "mf_corner_editor_" + hashlib.md5(_MF_EDITOR_HTML.encode("utf-8")).hexdigest()[:8])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f: f.write(_MF_EDITOR_HTML)
        reg.func = components.declare_component("mf_corner_editor", path=d)
    return reg.func(image=image, q=[float(v) for v in q], disabled=bool(disabled), key=key, default=None,
                    mask=[[float(x), float(y)] for x, y in mask] if mask else None, edit_mask=bool(edit_mask))

def _mf_on_mask_toggle():
    if st.session_state.get("mf_mask_on"):
        st.session_state["mf_preview_mode"] = "🖱️ 마우스로 직접 맞추기"   # 모양을 그리려면 마우스 화면이 필요

def _mf_fit_frame_to_mask(mk):
    """노란 틀(원근)을 건물 모양을 감싸는 사각형으로 맞춤(정면 사진용)"""
    m = st.session_state.get(mk)
    if not m: return
    xs, ys = [float(p[0]) for p in m], [float(p[1]) for p in m]
    st.session_state["mf_mode"] = "네 모서리"
    vals = {"mf_s_cx": (min(xs) + max(xs)) / 2, "mf_s_cy": (min(ys) + max(ys)) / 2, "mf_s_tw": max(xs) - min(xs), "mf_s_bw": max(xs) - min(xs),
            "mf_s_lh": max(ys) - min(ys), "mf_s_rh": max(ys) - min(ys), "mf_s_lean": 0, "mf_s_rise": 0}
    for k, v in vals.items():
        lo, hi = (-50, 50) if k in ("mf_s_lean", "mf_s_rise") else ((0, 100) if k in ("mf_s_cx", "mf_s_cy") else (2, 100))
        st.session_state[k] = int(max(lo, min(hi, round(v))))

def _mf_reset_mask(mk):
    st.session_state[mk] = None

def _sv(key, v):
    """위젯 값을 코드에서 바꿔 넣은 경우(마우스 조절) 기본값을 다시 넘기지 않도록"""
    return {} if key in st.session_state else {"value": v}

@st.cache_data(show_spinner=False, max_entries=10)
def mf_cached_facade_gif(site, sketches, secs, facade_items):
    """건물 합성 4컷 연속 재생 영상(같은 설정이면 다시 만들지 않음)"""
    frames = [mf_cached_composite(site, s, facade_items) for s in sketches]
    return make_facade_gif(frames, list(secs))


# 🚀 기본 설정 및 페이지 구성 (사이드바 강제 열림)
st.set_page_config(page_title="수업 및 활동 어시스트 프로그램", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.stMarkdown h1 { font-size: 34px !important; font-weight: 900 !important; color: #000000 !important; margin-bottom: 20px !important; }
.stMarkdown h2 { font-size: 28px !important; font-weight: 900 !important; color: #000000 !important; margin-top: 10px !important; margin-bottom: 15px !important; padding-bottom: 8px !important; border-bottom: 2px solid #dddddd !important; }
.stMarkdown h3 { font-size: 24px !important; font-weight: 800 !important; color: #111111 !important; margin-top: 25px !important; margin-bottom: 10px !important; }
div[data-testid="stMarkdownContainer"] > p, div[data-testid="stMarkdownContainer"] > ul > li { font-size: 16px !important; font-weight: 500 !important; color: #333333 !important; line-height: 1.6 !important; }
.stMarkdown strong, .stMarkdown b { font-weight: 700 !important; color: #000000 !important; }
[data-testid="stFormSubmitButton"] button, button[kind="primary"] { background-color: #FF4B4B !important; border: none !important; border-radius: 8px !important; min-height: 50px !important; width: 100% !important; padding: 12px !important; }
[data-testid="stFormSubmitButton"] button p, button[kind="primary"] p, [data-testid="stFormSubmitButton"] button div, button[kind="primary"] div { color: #ffffff !important; font-size: 18px !important; font-weight: 800 !important; }
button[kind="secondary"] { background-color: #3498db !important; border: none !important; border-radius: 8px !important; min-height: 50px !important; width: 100% !important; padding: 12px !important; }
button[kind="secondary"] p, button[kind="secondary"] div { color: #ffffff !important; font-size: 18px !important; font-weight: 800 !important; }
[data-testid="stForm"] { border: none !important; box-shadow: none !important; padding: 0 !important; }
.guide-box { background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin-bottom: 20px; border-left: 4px solid #3498db; }
</style>
""", unsafe_allow_html=True)

def get_kst_now(): return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

USERS_FILE, DATA_FILE, CONFIG_FILE = "users.json", "learning_data.json", "config.json"
UPLOAD_DIR, BACKUP_DIR = "uploads", "backups"
os.makedirs(UPLOAD_DIR, exist_ok=True); os.makedirs(BACKUP_DIR, exist_ok=True)

SUBJECTS = ["3학년 여행지리", "2학년 도시의 미래 탐구"]
CLASSES_MAP = {"3학년 여행지리": ["3B(3-6반)", "3A(3-8반)"], "2학년 도시의 미래 탐구": ["2G(2-1반)", "2H(2-2반)", "2I(2-8반)"]}
ADMIN_ACCOUNTS = {"audskal": {"pw": "1847", "name": "김명남(관리자)"}}

ACT_3_1 = "[3학년] 수행평가 1 - 영상으로 떠나는 여행"
ACT_3_2 = "[3학년] 수행평가 2 - 나를 성장시킨 장소 지도 만들기"
ACT_3_3 = "[3학년] 수행평가 3 - 나의 세계관에 대해 알아가는 '여행'"
ACT_2_1 = "[2학년] 수행평가 1 - 도시 '밈' 해석을 통한 도시성과 생활양식 탐구"
ACT_2_2 = "[2학년] 수행평가 2 - 내가 설계하는 N분 도시 with 파리의 15분 도시설계"
ACT_2_3 = "[2학년] 수행평가 3 - 빛으로 우리 지역을 말하다 - 내가 만드는 미디어 파사드"

ACTIVITIES = [ACT_3_1, ACT_3_2, ACT_3_3, ACT_2_1, ACT_2_2, ACT_2_3]
TIME_OPTIONS = [f"{str(h).zfill(2)}:{str(m).zfill(2)}" for h in range(24) for m in range(0, 60, 10)]
db_lock = threading.RLock()

def get_time_index(t_str): return TIME_OPTIONS.index(t_str) if t_str in TIME_OPTIONS else 0
def encode_token(user_key): return base64.b64encode(user_key.encode('utf-8')).decode('utf-8')
def decode_token(token):
    try: return base64.b64decode(token.encode('utf-8')).decode('utf-8')
    except: return None
def change_page(page_name):
    st.session_state.current_page = page_name
    st.query_params["current_page"] = page_name
    if st.session_state.get("logged_in") and st.session_state.get("user_info"):
        st.query_params["session_token"] = encode_token(st.session_state.user_info["user_key"])
    st.rerun()

def render_privacy_policy():
    with st.expander("📜 개인정보 처리방침 (수업용 웹 앱)", expanded=False):
        st.markdown("**[신선여자고등학교 수업용 웹 앱 개인정보 처리방침]**\n**1. 개인정보 수집 목적**: 교과 수업 운영, 학생 수행평가 과제물 제출/취합, 피드백 제공 및 학교생활기록부 기재 증빙 자료 활용\n**2. 수집 항목**: 필수 항목(과목, 반, 학번, 이름, 비밀번호, 과제물 데이터). ※ 주민등록번호 등 민감정보 일체 수집 불가\n**3. 보유 및 이용 기간**: 해당 학년도 교육과정 종료 시(익년 2월 말) 데이터 일괄 파기\n**4. 안전성 확보 조치**: 비밀번호 암호화/비노출, 교사-학생 권한 분리, 실시간 자동 스냅샷 백업 시스템 운영\n**5. 권리 행사**: 학생은 언제든지 자신의 개인정보 열람/정정/삭제를 요구할 수 있으며 담당 교사가 즉시 처리함.\n**6. 제3자 제공**: 수집된 학생 정보를 외부에 절대 제공하거나 위탁하지 않음.\n**7. 만 14세 미만 아동을 위한 절차**: 본 서비스는 고등학교 교육 활동용 서비스로서 만 14세 미만 아동의 개인정보를 수집·이용하지 않습니다.만약 만 14세 미만 아동의 개인정보를 수집할 경우 법정대리인의 법적 동의를 받습니다.\n**8. 책임자**: 교사 김명남 / 신선여자고등학교")

def check_active(act_name, class_group):
    config = load_json(CONFIG_FILE, {})
    deadlines = config.get("deadlines", {}).get(act_name, {}).get(class_group, {})
    if not deadlines: return True, "💡 교사가 아직 수업 시간표를 설정하지 않았습니다. (현재 자유 입력 가능)"
    final_dl_str = deadlines.get("final_dl", "2030-12-31 23:59")
    try: final_dl = datetime.datetime.strptime(final_dl_str, "%Y-%m-%d %H:%M")
    except: final_dl = datetime.datetime.max
    now = get_kst_now()
    if now > final_dl: return False, f"🚫 최종 제출 기한({final_dl_str})이 마감되어 더 이상 작성하거나 수정할 수 없습니다."
    slots = deadlines.get("slots", [])
    day_map = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    current_day = day_map[now.weekday()]
    current_time = now.time()
    schedule_strs, is_time_match, grace_left = [], False, None
    for slot in slots:
        if slot['day'] != "선택안함":
            p_str = f" {slot.get('period', '')}" if slot.get('period', '') and slot.get('period') != "선택안함" else ""
            schedule_strs.append(f"{slot['day']}요일{p_str} {slot['start']} - {slot['end']}")
            if slot['day'] == current_day:
                try:
                    s_t = datetime.datetime.strptime(slot["start"], "%H:%M").time()
                    e_dt = datetime.datetime.combine(now.date(), datetime.datetime.strptime(slot["end"], "%H:%M").time())
                    if s_t <= current_time <= e_dt.time(): is_time_match = True
                    elif e_dt < now <= e_dt + datetime.timedelta(minutes=GRACE_MINUTES):
                        # 🛟 수업 종료 직후 작성 중이던 내용이 사라지지 않도록 유예 시간 동안 저장 허용
                        left = int(((e_dt + datetime.timedelta(minutes=GRACE_MINUTES)) - now).total_seconds() // 60) + 1
                        grace_left = left if grace_left is None else max(grace_left, left)
                except: continue
    sched_display = ", ".join(schedule_strs) if schedule_strs else "설정된 수업 시간 없음"
    if is_time_match: return True, "✅ 현재 수업 시간입니다. 정상적으로 작성하고 저장(제출)할 수 있습니다."
    if grace_left is not None: return True, f"⏰ 수업 시간이 끝났습니다. 작성 중인 내용을 약 {grace_left}분 안에 꼭 [저장하기] 해 주세요."
    return False, f"⏳ 현재는 정해진 수업 시간이 아닙니다. 지정된 수업 시간에만 입력할 수 있습니다.\n\n(나의 주간 수업 시간: {sched_display} / 최종 기한: {final_dl_str})"

def check_active_with_exception(act_name, class_group, user_key):
    config = load_json(CONFIG_FILE, {})
    exceptions = config.get("exceptions", {}).get(act_name, {})
    if user_key in exceptions:
        try:
            exc_dl = datetime.datetime.strptime(exceptions[user_key], "%Y-%m-%d %H:%M")
            if get_kst_now() <= exc_dl: return True, f"✅ [개별 기한 연장] 특별 권한으로 정상 작성 및 제출이 가능합니다. (마감: {exceptions[user_key]})"
            else: return False, f"🚫 [개별 연장 기한 마감] 연장된 기한({exceptions[user_key]})이 지났습니다."
        except: pass
    return check_active(act_name, class_group)

def is_act_visible_for_class(act_name, class_group, config):
    vis_data = config.get("activity_visibility", {}).get(act_name, {})
    if isinstance(vis_data, dict): return vis_data.get(class_group, True)
    elif isinstance(vis_data, bool): return vis_data
    return True

def is_act_visible_for_user(act_name, class_group, user_key, config):
    exceptions = config.get("exceptions", {}).get(act_name, {})
    if user_key in exceptions:
        try:
            if get_kst_now() <= datetime.datetime.strptime(exceptions[user_key], "%Y-%m-%d %H:%M"): return True
        except: pass
    return is_act_visible_for_class(act_name, class_group, config)

# =====================================================================
# 💾 [안전 저장 엔진 연결] storage.py
#  - 동시 저장 시 '바뀐 항목만' 반영 → 다른 학생 자료를 덮어쓰지 않음
#  - 파일 손상 시 빈 데이터로 덮어쓰지 않고 백업에서 복원
#  - (선택) 구글 시트에 실시간 복제 → 서버가 초기화돼도 자동 복원
# =====================================================================
DRAFTS_FILE = "drafts.json"
GRACE_MINUTES = 10   # 수업 종료 후에도 저장을 허용하는 유예 시간(분)
_NS_BY_PATH = {USERS_FILE: "users", DATA_FILE: "data", CONFIG_FILE: "config", DRAFTS_FILE: "drafts"}

def _sheets_conf():
    """.streamlit/secrets.toml 에 구글 시트 정보가 있으면 사용, 없으면 로컬 파일만 사용"""
    try:
        if "gcp_service_account" in st.secrets and "gsheet_url" in st.secrets:
            return dict(st.secrets["gcp_service_account"]), str(st.secrets["gsheet_url"])
    except Exception:
        pass
    return None

STORE = storage.get_store(
    paths={"users": USERS_FILE, "data": DATA_FILE, "config": CONFIG_FILE, "drafts": DRAFTS_FILE},
    depth={"users": 1, "data": 2, "config": 1, "drafts": 2},
    backup_dir=BACKUP_DIR,
    snapshot_names={"users": "users", "data": "learning_data", "config": "config"},
    sheets_conf=_sheets_conf(),
)

# ===================== ☁️ 구글 시트 용량 관리 =====================
# 구글 시트 문서 하나에 담을 수 있는 용량에는 한계가 있습니다. 영상(MP4·GIF)처럼 큰 파일까지 시트에 올리면
# "This document is too large to continue editing" 오류로 시트 저장이 모두 멈춥니다.
# → 일정 크기(SHEET_MEDIA_MAX 글자)보다 큰 첨부 파일은 시트에 올리지 않고 '표시'만 남깁니다.
#   (글·표·사진(스케치) 같은 일반 자료는 그대로 시트에 보관 / 영상은 앱 서버에 보관)
SHEET_MEDIA_MAX = 700_000          # 약 0.5MB. 스케치·사진은 보통 이보다 작고, 영상은 훨씬 큼
MEDIA_OMIT = "__MEDIA_OMITTED__"

def _is_omitted(v):
    return isinstance(v, str) and v.startswith(MEDIA_OMIT)

# 🎬 영상 파일은 메모리(1GB 한도)에 들고 있지 않고 서버 디스크의 파일로 보관, 자료에는 'mediafile:이름'만 저장
MEDIA_DIR = "media_files"
MEDIA_REF = "mediafile:"
_MIME_EXT = {"video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "image/gif": "gif", "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_EXT_MIME = {v: k for k, v in _MIME_EXT.items()}

def media_save_bytes(data, ext):
    import hashlib as _h
    os.makedirs(MEDIA_DIR, exist_ok=True)
    name = f"{_h.sha1(data).hexdigest()[:24]}.{ext}"
    path = os.path.join(MEDIA_DIR, name)
    if not os.path.exists(path):
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return MEDIA_REF + name

def media_from_datauri(v, min_len=200_000):
    """큰 data:… 문자열(영상·GIF)은 파일로 옮기고 짧은 참조로 바꿈"""
    if not (isinstance(v, str) and v.startswith("data:") and len(v) > min_len and ";base64," in v[:100]):
        return v
    try:
        mime = v[5:v.index(";")]
        return media_save_bytes(base64.b64decode(v.split(",", 1)[1]), _MIME_EXT.get(mime, "bin"))
    except Exception:
        return v

def media_path(v):
    if isinstance(v, str) and v.startswith(MEDIA_REF):
        p = os.path.join(MEDIA_DIR, os.path.basename(v[len(MEDIA_REF):]))
        return p if os.path.exists(p) else ""
    return ""

def _media_ok(v):
    """화면에 보여 줄 수 있는 첨부 파일(파일 경로 또는 data:…)만 돌려줌. 없어진 파일·시트 표시는 빈 값"""
    if not isinstance(v, str) or _is_omitted(v):
        return ""
    if v.startswith(MEDIA_REF):
        return media_path(v)
    return v

def media_is_video(src):
    t = str(src or "").lower()
    return t.startswith("data:video") or t.endswith((".mp4", ".webm", ".mov"))

def show_media(v, caption=None, width=None, use_container_width=False):
    src = _media_ok(v)
    if not src:
        return False
    if media_is_video(src):
        st.video(src)
    elif use_container_width:
        st.image(src, caption=caption, use_container_width=True)
    else:
        st.image(src, caption=caption, width=width)
    return True

def media_datauri(v):
    """내려받기(HTML)용: 파일이면 data:… 로 바꿔 줌"""
    src = _media_ok(v)
    if src and not src.startswith("data:"):
        try:
            ext = src.rsplit(".", 1)[-1].lower()
            with open(src, "rb") as f:
                return f"data:{_EXT_MIME.get(ext, 'application/octet-stream')};base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception:
            return ""
    return src

def _sheet_slim(v):
    if isinstance(v, str):
        if len(v) > SHEET_MEDIA_MAX and v.startswith("data:"):
            mime = v[5:v.find(";")] if ";" in v[:80] else "media"
            return f"{MEDIA_OMIT}:{mime}:{len(v)}"
        return v
    if isinstance(v, dict):
        return {k: _sheet_slim(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_sheet_slim(x) for x in v]
    return v

def _sheet_slim_all_media(v):
    if isinstance(v, str):
        if v.startswith("data:") and len(v) > 20_000:
            mime = v[5:v.find(";")] if ";" in v[:80] else "media"
            return f"{MEDIA_OMIT}:{mime}:{len(v)}"
        return v
    if isinstance(v, dict):
        return {k: _sheet_slim_all_media(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_sheet_slim_all_media(x) for x in v]
    return v

def _drop_omitted(v):
    """남아 있는 '시트에 안 올린 파일' 표시는 빈 값으로 (화면 오류 방지)"""
    if _is_omitted(v): return ""
    if isinstance(v, dict): return {k: _drop_omitted(x) for k, x in v.items()}
    if isinstance(v, list): return [_drop_omitted(x) for x in v]
    return v

def _restore_omitted(new, base):
    """임시 작성 내용 등에 '시트에 안 올린 표시'가 있으면, 같은 자리의 실제 파일(base)로 되돌림"""
    if _is_omitted(new):
        return base if isinstance(base, str) and base and not _is_omitted(base) else new
    if isinstance(new, dict) and isinstance(base, dict):
        return {k: _restore_omitted(v, base.get(k)) for k, v in new.items()}
    if isinstance(new, list) and isinstance(base, list):
        return [_restore_omitted(v, base[i] if i < len(base) else None) for i, v in enumerate(new)]
    return new

def _is_quota_err(e):
    t = str(e)
    return "429" in t or "Quota" in t or "RATE_LIMIT" in t

def _install_sheet_slimming():
    """저장 엔진(재시작 없이 유지되는 모듈)의 시트 쓰기 함수를 바꿔 끼움: 큰 첨부 제외 + 줄어든 칸 비우기 + 나눠 보내기"""
    SM = getattr(storage, "SheetsMirror", None)
    if SM is None or getattr(SM, "_slim_ver", 0) >= 2:
        return
    orig = getattr(SM, "_orig_push", None) or SM.push
    SM._orig_push = orig

    def push(self, ns, items):
        import time as _t
        if not items:
            return
        if ns == "data":
            items = [(k, ts, d, None if d else _sheet_slim(v)) for k, ts, d, v in items]
        elif ns == "drafts":   # 임시 작성 내용은 글만 시트에 보관(사진은 저장된 자료에 있음) → 시트 용량 절반 이하로
            items = [(k, ts, d, None if d else _sheet_slim_all_media(v)) for k, ts, d, v in items]
        ws = self._get_ws(ns)
        old = {}
        try:   # 예전 조각 수(D열) → 이번에 줄어든 만큼 뒤쪽 칸을 비워 용량을 줄임
            col_a, col_d = ws.col_values(1), ws.col_values(4)
            for i, k in enumerate(col_a[1:], start=2):
                if k:
                    try: old[k] = (i, int(col_d[i - 1]) if i - 1 < len(col_d) and str(col_d[i - 1]).strip() else 0)
                    except ValueError: old[k] = (i, 0)
        except Exception:
            pass
        for g in range(0, len(items), 25):     # 한꺼번에 많이 보내면 구글 쓰기 한도(분당 60회)에 걸리므로 나눠 보냄
            part = items[g:g + 25]
            for attempt in range(6):
                try:
                    orig(self, ns, part)
                    break
                except Exception as e:
                    if _is_quota_err(e) and attempt < 5:
                        _t.sleep(20 + attempt * 10)
                        continue
                    raise
            if len(items) > 25:
                _t.sleep(1.2)
        clears = []
        for k, ts, d, v in items:
            if k in old:
                r, n0 = old[k]
                n1 = 0 if d else -(-len(json.dumps(v, ensure_ascii=False)) // storage.CHUNK)
                if n0 > n1:
                    clears.append(f"{SM._col_letter(SM.FIRST_DATA_COL + n1)}{r}:{SM._col_letter(SM.FIRST_DATA_COL + n0 - 1)}{r}")
        for g in range(0, len(clears), 200):
            ws.batch_clear(clears[g:g + 200])

    SM.push = push
    SM._slim_ver = 2

_install_sheet_slimming()

def sheet_shrink_now(log=None):
    """현재 시트에서 큰 첨부 파일이 들어 있는 줄을 가볍게 다시 쓰고, 남는 열을 지움. 반환: (줄 수, 지운 열 수)"""
    sm = STORE.sheets
    if sm is None:
        raise RuntimeError("구글 시트가 연결되어 있지 않습니다.")
    rows_done, cols_deleted = 0, 0
    for ns in ("data", "drafts"):
        ws = sm._get_ws(ns)
        col_a, col_d = ws.col_values(1), ws.col_values(4)
        big = []
        for i, k in enumerate(col_a[1:], start=2):
            try: n = int(col_d[i - 1]) if i - 1 < len(col_d) and str(col_d[i - 1]).strip() else 0
            except ValueError: n = 0
            if k and n * storage.CHUNK > SHEET_MEDIA_MAX:
                big.append(k)
        items = []
        with STORE.lock:
            for k in big:
                m = STORE.meta[ns].get(k)
                if not m: continue
                v = None if m[1] else STORE._get_flat(ns, k)
                items.append((k, m[0], m[1] or v is None, v))
        if items:
            sm.push(ns, items)
            rows_done += len(items)
        # 남는 열 지우기
        col_d = ws.col_values(4)
        need = 0
        for x in col_d[1:]:
            try: need = max(need, int(x))
            except (ValueError, TypeError): pass
        keep = max(sm.FIRST_DATA_COL - 1 + need + 2, 12)
        ws2 = sm.sh.worksheet(ns)
        if ws2.col_count > keep:
            cols_deleted += ws2.col_count - keep
            ws2.delete_columns(keep + 1, ws2.col_count)
            sm.ws[ns] = sm.sh.worksheet(ns)
            sm.cols[ns] = sm.ws[ns].col_count
        if log: log(f"{ns}: 큰 첨부 {len(items)}줄 정리")
    return rows_done, cols_deleted

def sheet_switch_to(url):
    """새 구글 시트로 옮기기: 서버에 있는 최신 자료 전체를 새 시트에 올림 (재시작 없이)"""
    conf = _sheets_conf()
    if not conf:
        raise RuntimeError("secrets 에 서비스 계정(gcp_service_account) 정보가 없습니다.")
    new = storage.SheetsMirror(conf[0], url.strip())     # 열 수 없으면 여기서 오류
    with STORE.lock:
        STORE.sheets = new
        STORE.sheets_enabled = True
        STORE.sheets_ready = False         # 백그라운드에서 10초 안에 새 시트와 병합 → 서버 자료 전체를 새 시트로 올림
        STORE.sheets_disabled_reason = None
        STORE.sheet_backoff_until = 0.0
        STORE.sheet_fail_count = 0
        STORE.sheet_last_error = None
        STORE.report_dirty = True
        STORE.last_report = 0.0
        STORE.report_last_error = None
    return new.sh.title if hasattr(new.sh, "title") else url

def load_json(file_path, default_value):
    ns = _NS_BY_PATH.get(file_path)
    if ns is None:
        try:
            with open(file_path, "r", encoding="utf-8-sig") as f: return json.load(f)
        except Exception: return default_value
    data = STORE.load(ns)
    if not data and isinstance(default_value, dict) and default_value:
        data.update(default_value)
    return data

def save_json(file_path, data):
    ns = _NS_BY_PATH.get(file_path)
    if ns is None:
        with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=4)
        return
    STORE.save(ns, data)

def create_auto_backup(reason="자동 스냅샷", force=False):
    """저장할 때마다 7MB 전체를 복사하던 방식 → 변경이 있으면 최대 5분마다 1개 스냅샷.
    (각 저장 내용은 저널과 구글 시트에 즉시 기록되므로 개별 저장은 유실되지 않습니다.)"""
    try: STORE.request_snapshot(reason, force=force)
    except Exception: pass

# --- 학생 활동지: 서버 임시저장(draft) + 안전 제출 ---
def _canon_small(v):
    if isinstance(v, str) and len(v) > 100_000:
        return f"<<{len(v)}:{hash(v)}>>"          # 아주 긴 글(첨부 파일)은 길이+지문으로만 비교 → 메모리 절약
    if isinstance(v, dict): return {k: _canon_small(x) for k, x in v.items()}
    if isinstance(v, list): return [_canon_small(x) for x in v]
    return v

def _canon(obj):
    try: return json.dumps(_canon_small(obj), ensure_ascii=False, sort_keys=True, default=str)
    except Exception: return str(obj)

def apply_draft(user_key, category, ans, enabled=True):
    """저장(제출)하지 않은 임시 내용이 있으면 화면에 복원한다.
    ⚠️ 활동지를 보고 있는 동안에는 처음 연 시점의 내용으로 고정한다.
    (임시저장이 갱신될 때마다 표·입력칸의 바탕 자료가 바뀌면 Streamlit 이 입력 중인 내용을 버리고 다시 그리기 때문)
    다른 화면에 갔다 오거나 접속이 끊겼다 다시 들어오면 그때 최신 임시저장 내용으로 새로 복원한다."""
    fk = f"_frozen_ans::{user_key}::{category}"
    cur = f"{user_key}::{category}"
    if fk in st.session_state:          # 같은 화면에 머무는 동안은 처음 연 내용으로 고정 (화면 갱신이 중간에 끊겨도 유지)
        st.session_state["_render_now"] = cur
        if st.session_state.get(fk + "::note"): st.info(st.session_state[fk + "::note"])
        return st.session_state[fk]
    st.session_state.pop(fk + "::note", None)
    st.session_state["_render_now"] = cur
    merged = ans
    viewer = st.session_state.get("user_info", {}) or {}
    is_teacher_view = viewer.get("role") == "관리자" and viewer.get("user_key") != user_key
    if enabled:
        draft = STORE.get("drafts", user_key, sub=category)
        if draft and _canon(draft) != _canon(ans):
            if is_teacher_view:
                # 👩‍🏫 선생님 조회 화면은 '저장된 내용'(= 구글 시트 응답 탭과 같은 내용)을 그대로 보여 줌
                diff = [k for k in set(draft) | set(ans or {}) if _canon(draft.get(k)) != _canon((ans or {}).get(k))]
                try:
                    _ts = (STORE.meta.get("drafts", {}).get(f"{user_key}{storage.SEP}{category}") or [None])[0]
                    _when = storage._kst_str(_ts) if _ts else ""
                except Exception:
                    _when = ""
                _note = (f"📝 이 학생은 아직 [저장하기]를 누르지 않은 임시 작성 내용이 있습니다{(' (마지막 임시저장 ' + _when + ')') if _when else ''}. "
                         f"지금 화면은 **최종 저장된 내용**(구글 시트와 같은 내용)입니다. 학생이 다시 접속해 [저장하기]를 누르면 반영됩니다. (다른 항목 {len(diff)}개)")
                st.session_state[fk + "::note"] = _note
                st.info(_note)
            else:
                merged = dict(ans)
                kept = []
                for k, v in draft.items():
                    v = _restore_omitted(v, ans.get(k))
                    if k != "step4_video": v = _drop_omitted(v)
                    if _table_blank(v) and not _table_blank(ans.get(k)):
                        kept.append(k)          # 🛟 임시 내용의 표가 통째로 비어 있으면, 저장된 표를 지키고 덮어쓰지 않음
                        continue
                    merged[k] = v
                st.warning("📝 저장하지 않은 임시 작성 내용을 복원했습니다. 내용을 확인한 뒤 **수업 시간 안에 [저장하기]** 를 눌러야 최종 저장됩니다.", icon="♻️")
    st.session_state[fk] = merged
    return merged

def _table_blank(v):
    """표(줄 목록)인데 맨 왼쪽 열(항목 이름)을 빼고 모든 칸이 비어 있으면 True. 표가 아니면 False"""
    if not (isinstance(v, list) and v and all(isinstance(r, dict) for r in v)):
        return False
    for r in v:
        vals = list(r.values())[1:]
        if any(str(x).strip() not in ("", "None", "nan", "False", "☆☆☆☆☆") for x in vals):
            return False
    return True

def autosave_draft(user_key, category, new_ans, disabled_flag):
    """입력칸에서 다른 곳을 클릭할 때마다(화면 갱신 시) 서버에 임시저장. 접속이 끊겨도 내용이 남는다."""
    if disabled_flag or not user_key: return
    try:
        # 화면을 처음 열었을 때의 상태를 기준으로 삼아, 실제로 고친 내용이 있을 때만 임시저장
        base_key = f"_draft_base::{user_key}::{category}"
        now_c = _canon(new_ans)
        if base_key not in st.session_state:
            st.session_state[base_key] = now_c
            return
        if now_c == st.session_state[base_key]: return
        saved = STORE.get("data", user_key, sub=category)
        cur_draft = STORE.get("drafts", user_key, sub=category)
        if _canon(new_ans) == _canon(saved if saved is not None else {}):
            if cur_draft is not None: STORE.delete("drafts", user_key, sub=category)
        elif _canon(_sheet_slim(new_ans)) != _canon(cur_draft):
            STORE.set("drafts", user_key, _sheet_slim(new_ans), sub=category)     # 임시저장에는 큰 영상을 넣지 않음
    except Exception:
        pass

def commit_activity(user_key, category, new_ans, u_name, success_msg="🎉 저장 완료!"):
    """학생 1명 x 수행평가 1개만 저장(다른 학생 자료는 절대 건드리지 않음) 후 검증."""
    try:
        STORE.set("data", user_key, new_ans, sub=category)
        if _canon(STORE.get("data", user_key, sub=category)) != _canon(new_ans):
            raise RuntimeError("저장 확인 실패")
        STORE.delete("drafts", user_key, sub=category)
        st.session_state[f"_draft_base::{user_key}::{category}"] = _canon(new_ans)   # 저장한 상태를 기준으로, 다음 변경부터 바로 임시저장
        st.session_state[f"_frozen_ans::{user_key}::{category}"] = json.loads(json.dumps(new_ans, ensure_ascii=False, default=str))
        create_auto_backup(f"[{u_name}] {category} 저장")
        st.balloons(); st.success(success_msg)
        st.session_state["_autosave_clear"] = True
        return True
    except storage.StorageNotReady as e:
        st.error(f"⚠️ {e} (작성한 내용은 임시저장되어 있습니다.)")
    except Exception as e:
        st.error(f"⚠️ 저장 중 오류가 발생했습니다: {e}  — 화면의 내용은 지워지지 않았으니 잠시 후 다시 [저장하기]를 눌러 주세요.")
    return False

# --- 📊 구글 시트 '응답 보기' 탭: 수행평가마다 탭 1개, 학생 = 행, 문항 = 열 ---
import re as _re
def _sheet_cell(v):
    """시트 셀용 정리: 이미지/영상은 표시만, HTML 태그 제거, 5만 자 제한"""
    t = str(v if v is not None else "")
    if t.startswith("__MEDIA_OMITTED__") or t.startswith("mediafile:"):
        return "[영상 첨부 – 용량이 커서 앱에서만 확인]"
    if "<" in t:
        t = _re.sub(r"<video[^>]*>.*?</video>|<video[^>]*>", "[영상 첨부]", t, flags=_re.S)
        t = _re.sub(r"<img[^>]*>", "[이미지 첨부]", t)
        t = _re.sub(r"<br\s*/?>", "\n", t)
        t = _re.sub(r"<[^>]+>", " ", t)
    t = _re.sub(r"data:[a-z]+/[\w.+-]+;base64,[A-Za-z0-9+/=]+", "[첨부 파일]", t)
    return t.strip()[:45000]

def _sheet_tab_title(act):
    m = _re.match(r"\[(\d학년)\]\s*(수행평가\s*\d+)", act)
    if m: return f"응답_{m.group(1)}_{m.group(2).replace(' ', '')}"
    return "응답_" + _re.sub(r"[\[\]\*\?/\\:]", "", act)[:40]

_FORMSPEC_SRC = r'''# 앱 화면 코드에서 '소제목(Step) → 문항 이름 → 저장 키' 순서를 읽어 오는 도구 (응답 탭용)
import re

RENDER_FUNCS = {
    "ACT_3_1": "render_activity1_3th", "ACT_3_2": "render_activity2_3th", "ACT_3_3": "render_activity3_3th",
    "ACT_2_1": "render_activity1_2nd", "ACT_2_2": "render_activity2_2nd", "ACT_2_3": "render_activity3_2nd",
}
_WIDGETS = r"file_uploader|text_input|text_area|radio|selectbox|number_input|slider|select_slider|checkbox|date_input"
_EVENT = re.compile(
    r"<h3[^>]*>(?P<h3>.*?)</h3>"
    r"|st\.markdown\(\s*f?[\"']#{2,4}\s*(?P<md>[^\"'\n]+)"
    r"|(?<!def )(?P<group>render_group_members\()"
    r"|(?P<dfvar>\w+)\s*=\s*pd\.DataFrame\(\s*ans\.get\(\s*[\"'](?P<dfkey>[^\"']+)[\"']"
    r"|(?:(?<=\n)|^)[ \t]*(?P<dfvar2>\w+)[ \t]*=[ \t]*pd\.DataFrame\(\s*(?P<dfsrc>\w+)\s*[,)]"
    r"|(?:(?<=\n)|^)[ \t]*(?P<recvar>\w+)[ \t]*=[ \t]*ans\.get\(\s*[\"'](?P<reckey>[^\"']+)[\"']"
    r"|(?:st\.data_editor|grid_editor)\(\s*(?P<edvar>[\w.()\[\]'\"]+)"
    r"|(?:st|col\w*|c\d|\w+_col\w*)\.(?P<w>" + _WIDGETS + r")\(\s*f?[\"'](?P<label>[^\"']*)[\"']",
    re.S)
_TAG = re.compile(r"<[^>]+>")


def _func_src(src, name):
    i = src.find(f"def {name}(")
    if i < 0:
        return ""
    j = src.find("\ndef ", i + 10)
    return src[i:j if j > 0 else len(src)]


def _clean(t):
    t = _TAG.sub("", t or "").replace("&nbsp;", " ").replace("**", "")
    return re.sub(r"\s+", " ", t).strip()


def _scan(code, src, out, heading):
    var2key, pending = {}, []
    for m in _EVENT.finditer(code):
        if m.group("h3"):
            heading = _clean(m.group("h3"))
        elif m.group("md"):
            sub = _clean(m.group("md")).lstrip("# ").split(" (")[0].strip()
            step = heading.split(" › ")[0]
            heading = f"{step} › {sub}" if step else sub
        elif m.group("group"):
            heading = _scan(_func_src(src, "render_group_members"), src, out, "모둠 구성원")
        elif m.group("dfvar"):
            var2key[m.group("dfvar")] = m.group("dfkey")
        elif m.group("dfvar2"):
            k = var2key.get(m.group("dfsrc"))
            if k:
                var2key[m.group("dfvar2")] = k
                pending = [p for p in pending if p != k]
        elif m.group("recvar"):
            var2key[m.group("recvar")] = m.group("reckey")
            pending.append(m.group("reckey"))
        elif m.group("edvar"):
            v = re.match(r"\w+", m.group("edvar"))
            v = v.group(0) if v else ""
            if v == "pd":   # st.data_editor(pd.DataFrame(ans.get("key"...)))
                km = re.search(r"ans\.get\(\s*[\"']([^\"']+)", code[m.start():m.start() + 300])
                key = km.group(1) if km else None
            else:
                key = var2key.get(v)
            if key:
                out.append(("table", heading, "", key))
        elif m.group("w"):
            window = code[m.end():m.end() + 700]
            nxt = re.search(r"\n\s*\w+\s*=\s*(?:st|col\w*|c\d)\.|\n\s*st\.", window)
            if nxt:
                window = window[:nxt.start()]
            km = re.search(r"ans\.get\(\s*[\"']([^\"']+)", window)
            key = km.group(1) if km else (pending.pop(0) if pending else None)
            if key:
                out.append(("field", heading, _clean(m.group("label")).rstrip(":："), key))
    return heading


def build_specs(src, act_names):
    """act_names: {"ACT_3_1": 실제 수행평가 이름, ...} → {수행평가 이름: [(종류, 소제목, 문항, 키), ...]}"""
    specs = {}
    for const, fn in RENDER_FUNCS.items():
        name = act_names.get(const)
        if not name:
            continue
        out = []
        _scan(_func_src(src, fn), src, out, "")
        seen, uniq = set(), []
        for item in out:
            if item[3] in seen:
                continue
            seen.add(item[3]); uniq.append(item)
        specs[name] = uniq
    return specs


def _is_media(v):
    return isinstance(v, str) and v.startswith("data:")


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "✔" if v else ""
    if _is_media(v):
        return "[이미지/영상 첨부 – 앱에서 확인]"
    return str(v)


SKIP_KEYS = {"step4_facade", "step5_change"}   # 화면 설정값·자동 생성 요약은 응답 탭에 표시하지 않음
EXTRA_LABELS = {"step4_total_sec": "작품 전체 길이(초)"}
MEDIA_NAMES = {"step2_site_photo": "최종 건물 현장 사진", "step3_map_before": "변경 전 지도 스케치", "step3_map_after": "변경 후 지도 스케치",
               "step4_video": "미디어 파사드 시뮬레이션 영상"}


def json_key(v):
    import json
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(v)


def _table_rows(heading, recs, label_prefix=""):
    """앱 화면의 표 모양 그대로: 머리글 1줄 + 표의 행마다 1줄(칸은 가로로)"""
    out, header_done = [], False
    for i, rec in enumerate(recs if isinstance(recs, list) else []):
        if not isinstance(rec, dict):
            continue
        cols = list(rec.keys())
        if not cols or all(_cell(rec.get(c)).strip() == "" for c in cols):
            continue
        if not header_done:
            out.append(("head", heading, label_prefix + str(cols[0]), [str(c) for c in cols[1:]]))
            header_done = True
        first = _cell(rec.get(cols[0])).strip() or f"{i + 1}행"
        out.append(("row", heading, label_prefix + first, [_cell(rec.get(c)) for c in cols[1:]]))
    return out


def rows_for(spec, ans):
    """반환: [(종류 head|row|field, 소제목, 문항, [답변 칸들])] — 앱 화면 순서 그대로"""
    rows, used = [], set()
    for kind, heading, label, key in spec:
        used.add(key)
        if key not in ans:
            continue
        val = ans.get(key)
        if kind == "table" or (isinstance(val, list) and val and isinstance(val[0], dict)):
            rows += _table_rows(heading, val)
        else:
            rows.append(("field", heading, label, [_cell(val)]))
    # 화면 코드에서 못 찾은 항목도 빠짐없이 끝에 표시
    used_vals = {json_key(ans.get(k)) for k in used if k in ans}
    for k, v in ans.items():
        if k in used:
            continue
        is_tbl = isinstance(v, list) and v and isinstance(v[0], dict)
        if _is_media(v):
            rows.append(("field", "첨부 자료", MEDIA_NAMES.get(k, "첨부 이미지"), [_cell(v)]))
        elif is_tbl and any(_is_media(x) for r in v for x in r.values()):
            for i, rec in enumerate(v):
                for c, x in rec.items():
                    if _is_media(x):
                        rows.append(("field", "첨부 자료", f"{i + 1}번 {c}", [_cell(x)]))
        elif is_tbl:
            if json_key(v) not in used_vals:
                rows += _table_rows("기타(이전 양식 항목)", v, f"{k} · ")
        elif _cell(v).strip() and json_key(v) not in used_vals:
            rows.append(("field", "기타(이전 양식 항목)", k, [_cell(v)]))
    return rows


def rows_wide(spec, ans):
    """앱 화면처럼 표를 가로로: [(종류, 소제목, [칸들])], 종류 = field | thead | trow"""
    out, used = [], set()

    def add_table(sec, recs):
        recs = [r for r in recs if isinstance(r, dict)]
        cols = []
        for r in recs:
            for c in r.keys():
                if c not in cols:
                    cols.append(c)
        if "장면 설명" in cols and "장면 설명 · 사용 기술 · 소요 시간" in cols:   # 합쳐 둔 요약 열은 중복이라 생략
            cols.remove("장면 설명 · 사용 기술 · 소요 시간")
        body = [r for r in recs if any(_cell(r.get(c)).strip() for c in cols)]
        if not cols or not body:
            return
        out.append(("thead", sec, [{"스케치_img": "스케치"}.get(str(c), str(c)) for c in cols]))
        for r in body:
            out.append(("trow", sec, [_cell(r.get(c)) for c in cols]))

    for kind, heading, label, key in spec:
        used.add(key)
        if key not in ans:
            continue
        val = ans.get(key)
        if kind == "table" or (isinstance(val, list) and val and isinstance(val[0], dict)):
            add_table(heading, val if isinstance(val, list) else [])
        else:
            out.append(("field", heading, [label, _cell(val)]))
    used_vals = {json_key(ans.get(k)) for k in used if k in ans}
    for k, v in ans.items():
        if k in used or k in SKIP_KEYS or json_key(v) in used_vals:
            continue
        if _is_media(v):
            out.append(("field", "첨부 자료", [MEDIA_NAMES.get(k, "첨부 이미지"), _cell(v)]))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            add_table(f"기타(이전 양식) · {k}", v)
        elif _cell(v).strip():
            out.append(("field", "기타 항목" if k in EXTRA_LABELS else "기타(이전 양식 항목)", [EXTRA_LABELS.get(k, k), _cell(v)]))
    return out
'''
if "_embedded_formspec" not in _sys.modules:
    _fm = _types.ModuleType("_embedded_formspec")
    exec(compile(_FORMSPEC_SRC, "embedded_formspec.py", "exec"), _fm.__dict__)
    _sys.modules["_embedded_formspec"] = _fm
_formspec = _sys.modules["_embedded_formspec"]
_SPEC_CACHE = {}

def _activity_specs():
    """앱 화면 코드에서 문항 이름·순서·Step 제목을 읽어 옴(파일이 바뀌면 다시 읽음)"""
    try:
        path = os.path.abspath(__file__); mt = os.path.getmtime(path)
        if _SPEC_CACHE.get("mt") != mt:
            src = open(path, encoding="utf-8").read()
            names = {"ACT_3_1": ACT_3_1, "ACT_3_2": ACT_3_2, "ACT_3_3": ACT_3_3, "ACT_2_1": ACT_2_1, "ACT_2_2": ACT_2_2, "ACT_2_3": ACT_2_3}
            _sp = _formspec.build_specs(src, names)
            if "MF_SHEET_SPEC" in globals(): _sp[ACT_2_3] = MF_SHEET_SPEC   # 미디어 파사드는 직접 정한 순서 사용
            _SPEC_CACHE.update(mt=mt, specs=_sp)
        return _SPEC_CACHE.get("specs", {})
    except Exception:
        return {}

def _sheet_report_hook(store):
    """앱 화면과 같은 모양: 일반 문항은 '문항 | 답변', 표는 머리글 줄 + 가로로 펼친 행.
    같은 학생 안에서 소제목은 처음 한 번만 표시(반·학번·이름은 필터용으로 모든 줄에 유지)"""
    data, users, cfg = store.load("data"), store.load("users"), store.load("config")
    specs = _activity_specs()
    errors = []
    for subj, acts in cfg.get("subject_activities", {}).items():
        studs = [(k, u) for k, u in users.items() if u.get("role") == "학생" and u.get("subject") == subj and u.get("approved", True)]
        studs.sort(key=lambda x: (str(x[1].get("class_group", "")), str(x[1].get("id", ""))))
        for act in acts:
            body, marks, groups = [], [], []   # marks: 표 머리글 줄(굵게) / groups: 학생별 (첫 줄, 마지막 줄)
            width = 3
            for k, u in studs:
                owner, ans = get_user_activity_data(k, u.get("id", ""), subj, u.get("class_group", ""), act, data)
                base = [u.get("class_group", ""), u.get("id", ""), u.get("name", "")]
                g_first = len(body) + 2                              # 시트의 실제 줄 번호(1행은 머리글)
                if not ans:
                    body.append(base + ["미제출", "", "", "", "", ""]); groups.append((g_first, g_first)); continue
                ans = _clean_nan(ans)
                if act == ACT_2_3: ans = mf_normalize_ans(ans)      # 구글 시트에도 지금 항목 이름으로 표시
                ts = store.meta_ts("data", f"{owner}||{act}")
                saved = (datetime.datetime.utcfromtimestamp(ts) + datetime.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M") if ts else ""
                writer = "본인" if owner == k else f"모둠 대표 {owner.split('_')[-1]}"
                head = base + ["제출", saved, writer]
                if act in specs:
                    items = _formspec.rows_wide(specs[act], ans)
                else:   # 문항 제작 탭으로 만든 활동지 등
                    items, sec = [], ""
                    for row in get_act_csv_rows(act, ans, cfg):
                        q, a = str(row[0]).strip(), (row[1] if len(row) > 1 else "")
                        if not q: continue
                        if a == "" and q.startswith("["): sec = q.strip("[]"); continue
                        items.append(("field", sec, [q, a]))
                prev_sec = None
                for kind, sec, cells in items:
                    show_sec = sec if sec != prev_sec else ""      # 같은 소제목은 맨 위 한 번만
                    prev_sec = sec
                    cells = [_sheet_cell(c) for c in cells]
                    width = max(width, len(cells) + 1)
                    if kind == "thead": marks.append(len(body) + 2)   # 시트의 실제 줄 번호(1행은 머리글)
                    body.append(head + [show_sec] + cells)
                groups.append((g_first, len(body) + 1))
            extra = ["표 3열", "표 4열", "표 5열", "표 6열", "표 7열", "표 8열", "표 9열", "표 10열"]
            header = ["반", "학번", "이름", "제출 여부", "마지막 저장", "작성자", "소제목", "문항 (표: 1열)", "답변 (표: 2열)"] + extra[:max(0, width - 3)]
            try:   # 한 탭에서 오류가 나도 나머지 탭은 계속 갱신
                store.sheets.write_table(_sheet_tab_title(act), [header] + body, freeze_cols=3, bold_rows=marks, groups=groups)
            except Exception as _e:
                errors.append(f"{_sheet_tab_title(act)}: {type(_e).__name__}: {str(_e)[:160]}")
                if "429" in str(_e) or "Quota" in str(_e):
                    __import__("time").sleep(20)          # 구글 시트 쓰기 한도 초과 → 잠시 쉬었다가 다음 탭
    store.report_tab_errors = errors
    if errors:
        raise RuntimeError(" / ".join(errors))    # 30초 뒤 실패한 탭만 다시 시도(성공한 탭은 내용이 같아 건너뜀)

STORE.report_hook = _sheet_report_hook
# ⚙️ 이미 실행 중인 저장 엔진의 작업 주기를 늦춰 CPU 절약 (재시작 없이 적용)
for _n, _v in (("FLUSH_EVERY", 10.0), ("REPORT_EVERY", 120.0), ("SNAPSHOT_EVERY", 900)):
    try: setattr(storage, _n, _v)
    except Exception: pass
# 앱 코드(app.py)가 바뀌면 학생 입력이 없어도 응답 탭을 한 번 새로 씀 (항목 이름 변경 등이 시트에 바로 반영되도록)
try:
    _app_code_sig = __import__("hashlib").md5(open(__file__, "rb").read()).hexdigest()
    if getattr(STORE, "_report_code_sig", None) != _app_code_sig:
        STORE._report_code_sig = _app_code_sig
        STORE.report_dirty = True
        STORE.last_report = 0.0
except Exception:
    pass

def request_report_refresh(wait=25):
    """선생님 버튼: 응답 탭을 지금 새로 쓰도록 요청하고 끝날 때까지 잠시 기다림"""
    import time as _t
    before = STORE.report_last_ok
    STORE.report_dirty = True
    STORE.last_report = 0.0
    t0 = _t.time()
    while _t.time() - t0 < wait:
        _t.sleep(1)
        if STORE.report_last_ok != before or STORE.report_last_error:
            break
    return STORE.report_last_ok != before, STORE.report_last_error

def init_system():
    with db_lock:
        users = load_json(USERS_FILE, {})
        users_changed = False
        for adm_id, adm_info in ADMIN_ACCOUNTS.items():
            if adm_id not in users or users[adm_id].get("password") != adm_info["pw"]:
                users[adm_id] = {"id": adm_id, "password": adm_info["pw"], "name": adm_info["name"], "role": "관리자", "subject": "전체", "class_group": "관리자", "approved": True}
                users_changed = True
        for k in [k for k in users.keys() if users[k].get("role") == "관리자" and k not in ADMIN_ACCOUNTS]: del users[k]; users_changed = True
        if users_changed: save_json(USERS_FILE, users)
        cfg = load_json(CONFIG_FILE, {})
        needs_update = False
        for key in ["materials", "notices", "custom_blocks", "dynamic_links"]:
            if key not in cfg: cfg[key] = []; needs_update = True
        for key in ["activity_visibility", "exceptions", "custom_forms", "deadlines"]:
            if key not in cfg: cfg[key] = {}; needs_update = True
        if "subject_activities" not in cfg:
            cfg["subject_activities"] = {"3학년 여행지리": [ACT_3_1, ACT_3_2, ACT_3_3], "2학년 도시의 미래 탐구": [ACT_2_1, ACT_2_2]}
            needs_update = True
        if ACT_2_3 not in cfg["subject_activities"].get("2학년 도시의 미래 탐구", []):
            cfg["subject_activities"].setdefault("2학년 도시의 미래 탐구", []).append(ACT_2_3); needs_update = True
        for k in ["tabs", "pdfs", "questions"]:
            if k in cfg: del cfg[k]; needs_update = True
        if needs_update: save_json(CONFIG_FILE, cfg)

def generate_points_html(df_records):
    if not df_records: return ""
    html = "<table style='width:100%; border-collapse:collapse; text-align:center;'><tr><th style='background-color:#ecf0f1; border:1px solid #bdc3c7; padding:10px;'>카테고리</th><th style='background-color:#ecf0f1; border:1px solid #bdc3c7; padding:10px;'>코드</th><th style='background-color:#ecf0f1; border:1px solid #bdc3c7; padding:10px;'>세부 개조 항목</th><th style='background-color:#ecf0f1; border:1px solid #bdc3c7; padding:10px;'>비용</th></tr>"
    i = 0
    while i < len(df_records):
        row = df_records[i]
        cat = str(row.get("카테고리", "")).strip()
        if cat:
            span = 1
            for j in range(i+1, len(df_records)):
                if not str(df_records[j].get("카테고리", "")).strip(): span += 1
                else: break
            html += f"<tr><td rowspan='{span}' style='text-align:center; font-weight:bold; vertical-align:middle; border:1px solid #bdc3c7; padding:10px;'>{cat}</td>"
        else: html += "<tr>"
        html += f"<td style='border:1px solid #bdc3c7; padding:10px;'>{row.get('코드','')}</td><td style='text-align:left; border:1px solid #bdc3c7; padding:10px;'>{row.get('세부 개조 항목','')}</td><td style='border:1px solid #bdc3c7; padding:10px;'>{row.get('비용','')}</td></tr>"
        i += 1
    return html + "</table><br>"

# ===================== 👥 모둠 활동: 대표 기록·잠금·정리 도구 =====================
import re as _gre

def _norm_id(s):
    """'20624천수민 ', '20624 천수민' → '20624' (학번 칸에 이름이 섞여도 숫자만 읽음)"""
    m = _gre.match(r"\s*(\d{4,5})", str(s or ""))
    return m.group(1) if m else str(s or "").strip()

def _same_id(a, b):
    """'2816' 과 '20816' 처럼 가운데 0이 빠진 학번도 같은 학생으로 봄"""
    a, b = _norm_id(a), _norm_id(b)
    if not a or not b: return False
    if a == b: return True
    if len(a) == 4 and len(b) == 5: a, b = b, a
    return len(a) == 5 and len(b) == 4 and a[:1] + a[2:] == b and a[1] == "0"

def _group_lock_on(act, cfg=None):
    cfg = cfg if cfg is not None else load_json(CONFIG_FILE, {})
    return bool(cfg.get("group_lock", {}).get(act, act == ACT_2_1))   # 2학년 수행평가1은 원래부터 잠금

def _rec_members(rec):
    return {_norm_id(rec.get(f"m{i}_id", "")) for i in range(1, 5)} - {""}

def _resolve_user_key(subj, cls, sid, users):
    k = f"{subj}_{cls}_{sid}"
    if k in users: return k
    for kk, u in users.items():
        if u.get("subject") == subj and u.get("class_group") == cls:
            uid = str(u.get("id", ""))
            if sid and (uid == sid or (len(uid) == 4 and uid[:1] + "0" + uid[1:] == sid) or (len(sid) == 4 and sid[:1] + "0" + sid[1:] == uid)):
                return kk
    return None

def get_user_activity_data(user_key, u_id, u_subj, u_class, act_name, learning_data):
    """모둠 활동: ① 선생님이 지정한 대표 기록 → ② 내 기록 → ③ 같은 모둠의 모둠장 기록 순서로 찾음"""
    if act_name not in [ACT_2_1, ACT_2_2]:
        return user_key, learning_data.get(user_key, {}).get(act_name, {})
    uid = _norm_id(u_id)
    own = learning_data.get(user_key, {}).get(act_name, {}) or {}
    if not uid:
        return user_key, own
    cfg = load_json(CONFIG_FILE, {})
    own_lid = _norm_id(own.get("m1_id", "")) if own else ""
    for gid, rk in (cfg.get("group_rep", {}).get(act_name, {}) or {}).items():
        cls, _, lid = gid.partition("|")
        if cls != u_class: continue
        rec = learning_data.get(rk, {}).get(act_name)
        if not rec: continue
        if rk == user_key or _same_id(lid, uid) or _same_id(own_lid, lid) or any(_same_id(uid, m) for m in _rec_members(rec)):
            return rk, rec
    if act_name == ACT_2_1:
        # 2학년 수행평가1은 이미 진행된 활동이라 예전 방식 그대로(선생님이 대표를 지정한 경우만 위에서 바뀜)
        u_id_str = str(u_id).strip()
        if str(own.get("m1_id", "")).strip() == u_id_str and own: return user_key, own
        for k, acts in learning_data.items():
            if k.startswith(f"{u_subj}_{u_class}_"):
                members = [str(acts.get(act_name, {}).get(f"m{i}_id", "")).strip() for i in range(1, 5)]
                if u_id_str in members: return k, acts.get(act_name, {})
        return user_key, own
    if own and own_lid == uid:
        return user_key, own                      # 내가 모둠장인 기록
    lock = _group_lock_on(act_name, cfg)
    if own and not lock:
        return user_key, own                      # 잠금 전: 내가 저장한 곳의 내용을 그대로 보여 줌
    best = None
    for k, acts in learning_data.items():
        if not k.startswith(f"{u_subj}_{u_class}_"): continue
        rec = acts.get(act_name) or {}
        if uid in _rec_members(rec):
            is_leader_rec = _norm_id(rec.get("m1_id", "")) == str(k.rsplit("_", 1)[-1])
            if best is None or (is_leader_rec and not best[2]):
                best = (k, rec, is_leader_rec)
    if best: return best[0], best[1]
    return user_key, own

# ===================== 🩺 학생 자료 점검·복구 (선생님용) =====================
def _leaf_count(v):
    """채워진 글 칸 수(첨부 파일 제외) — 자료가 얼마나 들어 있는지 비교용"""
    if isinstance(v, dict): return sum(_leaf_count(x) for x in v.values())
    if isinstance(v, list): return sum(_leaf_count(x) for x in v)
    if isinstance(v, str): return 1 if v.strip() and not v.startswith("data:") and not _is_omitted(v) and v.strip() != "☆☆☆☆☆" else 0
    if isinstance(v, (int, float)) and not isinstance(v, bool): return 1 if v else 0
    return 0

def _uid_of(k):
    return str(k).split(storage.SEP)[0].rsplit("_", 1)[-1]

def sheet_find_records(student_key, act, url="", tab="data"):
    """구글 시트(현재 연결된 시트 또는 주소를 넣은 다른 시트)의 data 탭에서 이 학생(같은 학번)의 기록을 찾음.
    반환: [(학생키, 저장시각, 자료)]  — 큰 시트도 해당 줄만 읽음"""
    sm = STORE.sheets
    if sm is None:
        raise RuntimeError("구글 시트가 연결되어 있지 않습니다.")
    sh = sm.client.open_by_url(url.strip()) if url and url.strip() else sm.sh
    try:
        ws = sh.worksheet(tab)
    except Exception:
        return []
    col_a = ws.col_values(1)
    uid = _uid_of(student_key)
    out = []
    for i, k in enumerate(col_a[1:], start=2):
        if not k or not k.endswith(f"{storage.SEP}{act}"):
            continue
        if not (k.split(storage.SEP)[0] == student_key or _same_id(_uid_of(k), uid)):
            continue
        row = ws.row_values(i)
        try:
            ts = float(row[1]) if len(row) > 1 and row[1] else 0.0
            deleted = len(row) > 2 and str(row[2]).strip() in ("1", "TRUE", "True")
            n = int(row[3]) if len(row) > 3 and row[3] else 0
            val = None if deleted else json.loads("".join(row[5:5 + n]) or "null")
        except Exception:
            continue
        if val:
            out.append((k.split(storage.SEP)[0], ts, val))
    return out

def restore_student_record(student_key, act, value, note="구글 시트에서 되살림"):
    """선택한 자료로 이 학생의 저장 내용을 되돌림. 바꾸기 전 상태는 자동 백업(스냅샷)에 남김."""
    try: STORE.snapshot_now(f"복구 전 자동 백업: {student_key} / {act}")
    except Exception: pass
    cur = STORE.get("data", student_key, sub=act) or {}
    new = _restore_omitted(value, cur)              # 시트에 안 올린 영상 표시는 앱 서버의 실제 파일로 되돌림
    STORE.set("data", student_key, new, sub=act)
    if STORE.get("drafts", student_key, sub=act) is not None:
        STORE.delete("drafts", student_key, sub=act)
    st.session_state.pop("_adm_view_id", None)      # 화면 입력칸을 새 자료로 다시 그리도록
    return new

def render_student_doctor(selected_student, u_info_sel, acts):
    """선생님 조회 화면: 이 학생의 저장 내용·임시 작성 내용·구글 시트 기록을 비교하고 되살리기"""
    with st.expander("🩺 이 학생 자료 점검·복구 (앱에서 내용이 안 보일 때)", expanded=False):
        act = st.selectbox("점검할 수행평가", acts, index=len(acts) - 1 if acts else 0, key=f"doc_act")
        if not act:
            return
        meta = STORE.meta
        fk = f"{selected_student}{storage.SEP}{act}"
        saved = STORE.get("data", selected_student, sub=act)
        draft = STORE.get("drafts", selected_student, sub=act)
        def _when(ns, k):
            m = meta.get(ns, {}).get(k)
            return storage._kst_str(m[0]) if m and m[0] else "-"
        st.markdown(f"- **앱 서버에 저장된 내용**: 채운 칸 **{_leaf_count(saved)}개** · 마지막 저장 {_when('data', fk)}")
        if draft is not None:
            st.markdown(f"- **저장 안 한 임시 작성 내용**: 채운 칸 {_leaf_count(draft)}개 · 마지막 임시저장 {_when('drafts', fk)}")
            if st.button("🗑️ 임시 작성 내용 지우기 (저장된 내용은 그대로)", key=f"doc_del_draft"):
                STORE.delete("drafts", selected_student, sub=act)
                st.session_state.pop("_adm_view_id", None)
                st.success("임시 작성 내용을 지웠습니다. 학생이 다시 접속하면 저장된 내용이 보입니다.")
        else:
            st.markdown("- 저장 안 한 임시 작성 내용: 없음")
        uid = _uid_of(selected_student)
        others = [(k, acts_.get(act)) for k, acts_ in load_json(DATA_FILE, {}).items()
                  if k != selected_student and act in acts_ and _same_id(_uid_of(k), uid)]
        for k, v in others:
            st.markdown(f"- ⚠️ **같은 학번의 다른 계정 기록**: `{k}` · 채운 칸 {_leaf_count(v)}개 · 마지막 저장 {_when('data', f'{k}{storage.SEP}{act}')}")
            if st.button(f"↪️ 이 기록을 지금 계정으로 가져오기", key=f"doc_take_{k}"):
                restore_student_record(selected_student, act, v, "다른 계정 기록 가져오기")
                st.success("가져왔습니다. 화면이 새 자료로 다시 그려집니다."); st.rerun()
        st.markdown("---")
        st.markdown("**☁️ 구글 시트에 남아 있는 기록 찾기**")
        old_url = st.text_input("다른(예전) 구글 시트에서 찾으려면 주소 입력 (비우면 지금 연결된 시트)", key="doc_sheet_url",
                                placeholder="https://docs.google.com/spreadsheets/d/...")
        rkey = f"_doc_found::{selected_student}::{act}"
        if st.button("🔎 구글 시트에서 이 학생 기록 찾기", key="doc_find"):
            with st.spinner("구글 시트를 확인하는 중입니다..."):
                try:
                    st.session_state[rkey] = ([("저장된 내용",) + r for r in sheet_find_records(selected_student, act, old_url, "data")] +
                                              [("저장 안 한 임시 작성 내용",) + r for r in sheet_find_records(selected_student, act, old_url, "drafts")])
                    if not st.session_state[rkey]: st.warning("시트에서 이 학생의 기록(저장된 내용·임시 작성 내용)을 찾지 못했습니다.")
                except Exception as e:
                    st.error(f"시트를 읽을 수 없습니다: {type(e).__name__}: {str(e)[:200]}")
        for i, (kind, k, ts, val) in enumerate(st.session_state.get(rkey) or []):
            more = _leaf_count(val) - _leaf_count(saved)
            st.markdown(f"- 시트의 **{kind}** `{k}` · 저장 시각 {storage._kst_str(ts) if ts else '-'} · 채운 칸 **{_leaf_count(val)}개** "
                        f"({'앱보다 ' + str(more) + '칸 많음' if more > 0 else '앱과 같거나 적음'})")
            with st.popover("내용 미리 보기") if hasattr(st, "popover") else st.expander("내용 미리 보기"):
                for kk, vv in val.items():
                    if isinstance(vv, str) and (vv.startswith("data:") or _is_omitted(vv)): continue
                    st.markdown(f"**{kk}**")
                    if isinstance(vv, list): st.dataframe(pd.DataFrame(vv), hide_index=True)
                    else: st.write(vv)
            if st.button("♻️ 이 시트 기록으로 되살리기 (지금 앱 내용은 자동 백업 후 교체)", key=f"doc_restore_{i}"):
                restore_student_record(selected_student, act, val)
                st.session_state.pop(rkey, None)
                st.success("✅ 되살렸습니다. 화면이 새 자료로 다시 그려집니다. (바꾸기 전 내용은 자동 백업 센터에 남아 있습니다)")
                st.rerun()

def build_group_index(act, learning_data, users, subj):
    """반|모둠장학번 → {records: {학생키: 기록}, leader_key, members}"""
    groups = {}
    for k, acts in learning_data.items():
        rec = acts.get(act)
        u = users.get(k, {})
        if not rec or u.get("subject") != subj: continue
        lid = _norm_id(rec.get("m1_id", "")) or str(u.get("id", ""))
        lk = _resolve_user_key(subj, u.get("class_group", ""), lid, users)
        if lk: lid = str(users[lk].get("id", lid))          # 실제 계정 학번으로 맞춤(20816 → 2816)
        gid = f"{u.get('class_group', '')}|{lid}"
        g = groups.setdefault(gid, {"class": u.get("class_group", ""), "leader_id": lid, "records": {}, "members": set()})
        g["records"][k] = rec
        g["members"] |= _rec_members(rec) | {str(u.get("id", ""))}
    for gid, g in groups.items():
        g["leader_key"] = _resolve_user_key(subj, g["class"], g["leader_id"], users)
    return groups

def _filled_count(rec):
    n = 0
    for k, v in (rec or {}).items():
        if _gre.match(r"m\d_(id|name)$", k): continue
        if isinstance(v, str) and v.strip() and not v.startswith("data:"): n += 1
        elif isinstance(v, list):
            n += sum(1 for r in v if isinstance(r, dict) for x in r.values() if isinstance(x, str) and x.strip() and x not in ("-15pt", "-10pt"))
        elif isinstance(v, str) and v.startswith("data:"): n += 1
    return n

def _is_blank(v):
    if v is None: return True
    if isinstance(v, float) and v != v: return True
    if isinstance(v, str): return v.strip() in ("", "None", "nan")
    if isinstance(v, list): return all(isinstance(r, dict) and all(_is_blank(x) or x is False for x in r.values()) for r in v)
    return False

def merge_group_records(primary, others):
    """모둠 기록 합치기: 빈칸은 채워진 쪽으로, 서로 다른 내용은 [모둠원 학번 작성분] 표시와 함께 모두 남김"""
    out = json.loads(json.dumps(primary, ensure_ascii=False, default=str))
    for oid, rec in others:
        for k, v in rec.items():
            if _gre.match(r"m\d_(id|name)$", k) or _is_blank(v): continue
            cur = out.get(k)
            if _is_blank(cur):
                out[k] = v; continue
            if isinstance(v, str) and isinstance(cur, str):
                if v.strip() != cur.strip() and v.strip() not in cur and not v.startswith("data:"):
                    out[k] = f"{cur}\n\n[모둠원 {oid} 작성분]\n{v}"
            elif isinstance(v, list) and isinstance(cur, list):
                cur_rows = [json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) for r in cur]
                if len(v) == len(cur) and all(isinstance(a, dict) and isinstance(b, dict) for a, b in zip(cur, v)):
                    merged = []
                    for a, b in zip(cur, v):
                        row = dict(a)
                        for c, bv in b.items():
                            av = row.get(c)
                            if _is_blank(av) or av is False: row[c] = bv
                            elif isinstance(av, str) and isinstance(bv, str) and not _is_blank(bv) and bv.strip() != av.strip() and bv.strip() not in av:
                                row[c] = f"{av} / [{oid}] {bv}"
                        merged.append(row)
                    out[k] = merged
                else:
                    out[k] = cur + [r for r in v if json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) not in cur_rows and not _is_blank([r])]
    # 사본에만 적힌 모둠원도 합친 기록의 빈 모둠원 칸에 채워 넣음(모두 같은 기록을 보도록)
    have = _rec_members(out)
    for _, rec in others:
        for i in range(1, 5):
            mid = _norm_id(rec.get(f"m{i}_id", ""))
            if mid and mid not in have:
                slot = next((j for j in range(1, 5) if not _norm_id(out.get(f"m{j}_id", ""))), None)
                if slot is None: break
                out[f"m{slot}_id"], out[f"m{slot}_name"] = rec.get(f"m{i}_id", ""), rec.get(f"m{i}_name", "")
                have.add(mid)
    return out

def render_group_cleanup():
    """관리자: 모둠원이 따로 저장한 사본을 모둠장 기록과 나란히 보고, 대표 지정 또는 합치기"""
    with st.expander("🧩 모둠 기록 정리 (2학년 수행평가 1·2) — 모둠원이 따로 저장한 사본 확인·대표 지정·합치기", expanded=False):
        subj = "2학년 도시의 미래 탐구"
        act = st.selectbox("모둠 활동 선택", [ACT_2_2, ACT_2_1], key="gc_act")
        cfg = load_json(CONFIG_FILE, {})
        lock_now = _group_lock_on(act, cfg)
        new_lock = st.toggle("🔒 모둠원 조회 전용 잠금 (켜면 모둠 대표 기록의 작성자만 작성·저장할 수 있고, 나머지 모둠원은 조회만 함)",
                             value=lock_now, key=f"gc_lock_{act}")
        if new_lock != lock_now:
            fresh = load_json(CONFIG_FILE, {}); fresh.setdefault("group_lock", {})[act] = bool(new_lock); save_json(CONFIG_FILE, fresh)
            create_auto_backup(f"[{act[:12]}] 모둠 잠금 {'켬' if new_lock else '끔'}", force=True)
            st.rerun()
        st.caption("💡 아래 정리를 먼저 마친 뒤 잠금을 켜는 것을 권장합니다. 어느 경우에도 원래 기록은 지워지지 않고 보관됩니다.")
        users = load_json(USERS_FILE, {}); data = load_json(DATA_FILE, {})
        groups = build_group_index(act, data, users, subj)
        reps = cfg.get("group_rep", {}).get(act, {}) or {}
        cls_filter = st.radio("반", ["전체"] + CLASSES_MAP.get(subj, []), horizontal=True, key="gc_cls")
        todo = {gid: g for gid, g in groups.items()
                if (cls_filter == "전체" or g["class"] == cls_filter)
                and (len(g["records"]) > 1 or g["leader_key"] not in g["records"] or gid in reps)}
        if not todo:
            st.success("정리가 필요한 모둠이 없습니다. (모둠원 사본 없음)"); return
        st.markdown(f"**정리가 필요한 모둠 {len(todo)}개** — 모둠원 사본이 있거나, 모둠장 기록이 없는 모둠입니다.")
        for gid, g in sorted(todo.items()):
            lname = users.get(g["leader_key"] or "", {}).get("name", "")
            rep = reps.get(gid) or (g["leader_key"] if g["leader_key"] in g["records"] else None)
            st.markdown(f"---\n##### [{g['class']}] 모둠장 {g['leader_id']} {lname} 모둠 &nbsp; <span style='font-size:13px;color:#64748b'>구성원 학번: {', '.join(sorted(g['members']))}</span>", unsafe_allow_html=True)
            if g["leader_key"] not in g["records"]:
                st.warning("이 모둠은 모둠장 본인 기록이 없습니다. 아래 기록 중 하나를 대표로 지정하거나 합쳐 주세요.")
            keys = sorted(g["records"], key=lambda k: (k != g["leader_key"], -_filled_count(g["records"][k])))
            cols = st.columns(min(len(keys), 3))
            for i, k in enumerate(keys):
                rec = g["records"][k]; u = users.get(k, {})
                ts = STORE.meta_ts("data", f"{k}||{act}")
                when = (datetime.datetime.utcfromtimestamp(ts) + datetime.timedelta(hours=9)).strftime("%m-%d %H:%M") if ts else "-"
                with cols[i % len(cols)]:
                    role = "👑 모둠장 본인 기록" if k == g["leader_key"] else "👤 모둠원 사본"
                    star = " ✅ **현재 대표**" if k == rep else ""
                    st.markdown(f"**{role}**{star}  \n{u.get('id')} {u.get('name')} · 작성 {_filled_count(rec)}칸 · 저장 {when}")
                    with st.expander("내용 보기"):
                        for row in get_act_csv_rows(act, _clean_nan(rec), cfg):
                            q, a = str(row[0]), str(row[1] if len(row) > 1 else "")
                            if a.strip() and not a.startswith("<") and "data:" not in a:
                                st.markdown(f"- **{q}**: {a[:300]}")
                    if k != rep and st.button("이 기록을 모둠 대표로 지정", key=f"gc_rep_{act}_{gid}_{k}"):
                        fresh = load_json(CONFIG_FILE, {}); fresh.setdefault("group_rep", {}).setdefault(act, {})[gid] = k; save_json(CONFIG_FILE, fresh)
                        create_auto_backup(f"[{g['class']} {g['leader_id']} 모둠] 대표 기록 지정", force=True)
                        st.rerun()
            if len(keys) > 1:
                target = rep or keys[0]
                others = [(users.get(k, {}).get("id", k), g["records"][k]) for k in keys if k != target]
                merged = merge_group_records(g["records"][target], others)
                c1, c2 = st.columns([2, 1])
                with c1:
                    agree = st.checkbox(f"🔗 모두 합치기: {users.get(target, {}).get('name', '')}의 기록을 기준으로 나머지 {len(others)}개를 합칩니다. "
                                        f"(작성 {_filled_count(g['records'][target])}칸 → 합친 뒤 {_filled_count(merged)}칸, 원본은 보관)", key=f"gc_agree_{act}_{gid}")
                with c2:
                    if st.button("🔗 모두 합치기 실행", key=f"gc_merge_{act}_{gid}", disabled=not agree):
                        stamp = get_kst_now().strftime("%m%d_%H%M%S")
                        for k in keys:   # 합치기 전 원본을 모두 보관
                            STORE.set("data", k, g["records"][k], sub=f"[보관] {act} 합치기 전 {stamp}")
                        STORE.set("data", target, merged, sub=act)
                        fresh = load_json(CONFIG_FILE, {}); fresh.setdefault("group_rep", {}).setdefault(act, {})[gid] = target; save_json(CONFIG_FILE, fresh)
                        create_auto_backup(f"[{g['class']} {g['leader_id']} 모둠] 기록 합치기", force=True)
                        st.success("합쳤습니다. 이제 이 모둠의 모든 구성원에게 합친 기록이 보입니다."); st.rerun()
            if gid in reps and st.button("대표 지정 취소", key=f"gc_unrep_{act}_{gid}"):
                fresh = load_json(CONFIG_FILE, {}); fresh.get("group_rep", {}).get(act, {}).pop(gid, None); save_json(CONFIG_FILE, fresh)
                st.rerun()

def get_act_csv_rows(selected_view, ans, config=None):
    if ans is None:
        ans = {}
    csv_data = []

    # ----------------------------------------------------
    # 3학년 여행지리 수행평가 1
    # ----------------------------------------------------
    if selected_view == ACT_3_1:
        csv_data.extend([
            ["[Part 1. 영상 기본 정보]", ""],
            ["1. 영상의 제목", ans.get("a1_1", "").strip() or "(미작성)"],
            ["2. 영상에 등장하는 국가/지역", ans.get("a1_2", "").strip() or "(미작성)"],
            ["3. 영상을 선택하게 된 이유", ans.get("a1_3", "").strip() or "(미작성)"],

            ["[Part 2. 영상 감상 및 분석]", ""],
            ["1. 첫 느낌", ans.get("a2_1", "").strip() or "(미작성)"],
            ["▶ 인상적이었던 장소/공간", (ans.get("a2_2_1", "") or ans.get("a2_2", "")).strip() or "(미작성)"],
            ["▶ 이유", (ans.get("a2_2_2", "") or ans.get("a2_3", "")).strip() or "(미작성)"],
            ["▶ 누구에게 추천", ans.get("a2_3_1", "").strip() or "(미작성)"],
            ["▶ 추천하는 이유", ans.get("a2_3_2", "").strip() or "(미작성)"],
            ["4. 나만의 감상평", (ans.get("a2_4", "") if "a2_3_1" in ans else ans.get("a2_6", "")).strip() or "(미작성)"],

            ["[Part 3. 나만의 여행 영상 기획]", ""],
            ["1) 영상의 제목", ans.get("a3_1", "").strip() or "(미작성)"],
            ["2) 주요 컨셉/느낌", ans.get("a3_2", "").strip() or "(미작성)"],
            ["3) 누구와 함께 가고 싶은가?", ans.get("a3_3", "").strip() or "(미작성)"],
            ["4) 그 이유는?", ans.get("a3_4", "").strip() or "(미작성)"],
            ["5) 가장 해 보고 싶은 것", ans.get("a3_5", "").strip() or "(미작성)"],
            ["6) 그 이유는?", ans.get("a3_6", "").strip() or "(미작성)"],
            ["7) 꼭 넣고 싶은 장소/공간", ans.get("a3_7", "").strip() or "(미작성)"],
            ["8) 그 이유는?", ans.get("a3_8", "").strip() or "(미작성)"],
            ["9) 썸네일 영상 기획", ans.get("a3_9", "").strip() or "(미작성)"],
            ["10) 어울리는 BGM", ans.get("a3_10", "").strip() or "(미작성)"],
            ["11) 그 이유는?", ans.get("a3_11", "").strip() or "(미작성)"]
        ])

    # ----------------------------------------------------
    # 3학년 여행지리 수행평가 2
    # ----------------------------------------------------
    elif selected_view == ACT_3_2:  # ♣ [3학년] 수행평가 2 (변수명이 ACT_3_1이면 맞춰서 유지)
        csv_data.extend([
            ["[1. 편안함을 주는 장소]", ""],
            ["1-1) 나에게 편안함을 주는 장소가 있는가?", ans.get("q1_1", "").strip() or "(미작성)"],
            ["1-2) 어떤 면에서 편안함을 주는가?", ans.get("q1_2", "").strip() or "(미작성)"],

            ["[2. 성격과 장소]", ""],
            ["2-1) 자신의 성격은?", ans.get("q2_1", "").strip() or "(미작성)"],
            ["2-2) 성격 형성에 영향을 준 장소가 있는가?", ans.get("q2_2", "").strip() or "(미작성)"],
            ["2-3) 그 이유는 무엇 때문인가?", ans.get("q2_3", "").strip() or "(미작성)"],

            ["[3. 장점과 장소]", ""],
            ["3-1) 자신의 장점은?", ans.get("q3_1", "").strip() or "(미작성)"],
            ["3-2) 장점 형성에 영향을 준 장소가 있는가?", ans.get("q3_2", "").strip() or "(미작성)"],
            ["3-3) 그 이유는 무엇 때문인가?", ans.get("q3_3", "").strip() or "(미작성)"],

            ["[4. 성장과 장소]", ""],
            ["4-1) 성장함에 있어 영향을 준 장소가 있는가?", ans.get("q4_1", "").strip() or "(미작성)"],
            ["4-2) 어떤 면에서 영향을 주었는가?", ans.get("q4_2", "").strip() or "(미작성)"],

            ["[5. 목표와 장소]", ""],
            ["5-1) 지금 나의 목표는?", ans.get("q5_1", "").strip() or "(미작성)"],
            ["5-2) 목표 설정에 영향을 준 장소가 있는가?", ans.get("q5_2", "").strip() or "(미작성)"],

            ["[6. 소중한 사람에게 소개하고 싶은 장소]", ""],
            ["6-1) 소중한 사람에게 소개해 주고 싶은 장소가 있는가?", ans.get("q6_1", "").strip() or "(미작성)"],
            ["6-2) 그 이유는?", ans.get("q6_2", "").strip() or "(미작성)"],

            ["[7. 나만의 비밀 장소]", ""],
            ["7-1) 나만의 비밀 장소가 있는가?", ans.get("q7_1", "").strip() or "(미작성)"],
            ["7-2) 그 이유는?", ans.get("q7_2", "").strip() or "(미작성)"],

            ["[8. 다시 가고 싶은 장소]", ""],
            ["8-1) 과거로 돌아갈 수 있다면 다시 가고 싶은 장소는?", ans.get("q8_1", "").strip() or "(미작성)"],
            ["8-2) 그 이유는?", ans.get("q8_2", "").strip() or "(미작성)"]
        ])

    # ----------------------------------------------------
    # 3학년 여행지리 수행평가 3
    # ----------------------------------------------------
    elif selected_view == ACT_3_3:
        csv_data.extend([["[1. 세계 인식 수준에 대한 확인]", ""], ["[1. 대륙별 관심도 및 지식 수준 체크]", ""]])
        for row in ans.get("s1_df", []):
            csv_data.append([row.get("대륙", ""), f"관심도: {row.get('관심도', '')} / 지식수준: {row.get('지식수준', '')}"])
        
        csv_data.append(["[2. (개인적 경험에 기반) 특정 국가에 대한 기억과 인상에 대한 분석]", ""])
        csv_data.append(["[직접 경험]", ""])
        for row in ans.get("direct_df", []):
            csv_data.append([row.get("여행해 본 국가", ""), row.get("해당 국가에 대한 구체적인 기억 혹은 인상", "")])
        
        csv_data.append(["[간접 경험]", ""])
        csv_data.extend([
            ["즐겨 보는 외국 영화/드라마는 어느 나라 작품?", ans.get("ind1", "") or "(미작성)"],
            ["좋아하는 음악가나 연예인이 있다면 어느 나라?", ans.get("ind2", "") or "(미작성)"],
            ["자주 먹는 외국 음식이 있다면 어느 나라?", ans.get("ind3", "") or "(미작성)"]
        ])
        
        csv_data.append(["[3. 꼭 가보고 싶은 Top 5 국가와 그 이유]", ""])
        w_list = [r for r in ans.get("top5_want", []) if r.get("국가 혹은 지역") or r.get("이유")]
        if w_list:
            for row in w_list: 
                csv_data.append([row.get("국가 혹은 지역", ""), row.get("이유", "")])
        else:
            csv_data.append(["1~5위", "(미작성)"])
        
        csv_data.append(["[4. 절대 가고 싶지 않은 Top 5 국가와 그 이유]", ""])
        nw_list = [r for r in ans.get("top5_notwant", []) if r.get("국가 혹은 지역") or r.get("이유")]
        if nw_list:
            for row in nw_list: 
                csv_data.append([row.get("국가 혹은 지역", ""), row.get("이유", "")])
        else:
            csv_data.append(["1~5위", "(미작성)"])

        csv_data.extend([["", ""], ["[2. 특정 대륙/국가에 대한 자신의 편견과 고정관념]", ""], ["[1. 국가별 한 단어 라벨링]", ""]])
        for row in ans.get("label_df", []):
            if row.get("가 보고 싶은 국가") or row.get("가고 싶지 않은 국가"):
                csv_data.append([row.get("가 보고 싶은 국가", ""), f"한 단어 라벨: {row.get('한 단어 라벨', '')} | 가고 싶지 않은 국가: {row.get('가고 싶지 않은 국가', '')} (라벨: {row.get('한 단어 라벨(부정)', '')})"])
        
        csv_data.append(["[2. 개인적으로 가장 강한 편견을 가진 국가]", ""])
        for row in ans.get("prej_df", []):
            if row.get("국가명") or row.get("편견 내용"):
                csv_data.append([row.get("국가명", ""), f"편견 내용: {row.get('편견 내용', '')} / 이유: {row.get('편견 형성 과정 혹은 이유', '')}"])
        
        csv_data.extend([
            ["[3. 미디어와 교육의 영향으로 인한 인식 발견]", ""],
            ["뉴스에서 자주 접하는 국가들", f"{ans.get('media1_1', '') or '(미작성)'} (이미지: {ans.get('media1_2', '')})"],
            ["영화/드라마에서 자주 접하는 국가들", f"{ans.get('media2_1', '') or '(미작성)'} (이미지: {ans.get('media2_2', '')})"],
            ["학교에서 많이 배운 국가들", f"{ans.get('media3_1', '') or '(미작성)'} (지식: {ans.get('media3_2', '')})"]
        ])
        
        csv_data.append(["[4. 부정확한 정보나 과장된 인식 발견]", ""])
        for row in ans.get("fake_df", []):
            if row.get("국가명") or row.get("잘못 알고 있었던 내용"):
                csv_data.append([row.get("국가명", ""), f"잘못 알고 있던 내용: {row.get('잘못 알고 있었던 내용', '')} / 실제 사실: {row.get('실제 사실', '')}"])
        
        csv_data.append(["[5. 우월감이나 차별 의식 점검]", ""])
        for row in ans.get("discrim_df", []):
            if row.get("어떤 국가에 대해?") or row.get("어떤 측면에서"):
                csv_data.append([row.get("어떤 국가에 대해?", ""), f"측면: {row.get('어떤 측면에서', '')} / 이유: {row.get('그 이유', '')}"])

        csv_data.extend([["", ""], ["[3. 포용적이고 균형잡힌 세계관을 위한 노력]", ""]])
        csv_data.append(["[1. 편견을 바꾸고 싶은 국가]", ""])
        c_list = [r for r in ans.get("change_df", []) if r.get("어떤 국가에 대해?") or r.get("현재의 편견")]
        if c_list:
            for row in c_list: 
                csv_data.append([row.get("어떤 국가에 대해?", ""), f"현재 편견: {row.get('현재의 편견', '')} / 계획: {row.get('올바른 정보를 찾기 위한 계획', '')}"])
        else:
            csv_data.append(["작성 내용", "(미작성)"])
        
        csv_data.append(["[2. 가장 무관심했던 대륙 혹은 국가]", ""])
        ig_list = [r for r in ans.get("ignore_df", []) if r.get("선택 대륙/국가") or r.get("무관심 이유")]
        if ig_list:
            for row in ig_list: 
                csv_data.append([row.get("선택 대륙/국가", ""), f"무관심 이유: {row.get('무관심 이유', '')} / 정보 수집 방법: {row.get('관심 확장을 위한 정보 수집 방법', '')}"])
        else:
            csv_data.append(["작성 내용", "(미작성)"])
        
        csv_data.append(["[3. 서구 중심적 시각에서 벗어나기]", ""])
        w_list = [r for r in ans.get("western_df", []) if r.get("현재 가지고 있는 서구 중심적 시각") or r.get("개선 방법")]
        if w_list:
            for row in w_list: 
                csv_data.append([row.get("현재 가지고 있는 서구 중심적 시각", ""), f"개선 방법: {row.get('개선 방법', '')}"])
        else:
            csv_data.append(["작성 내용", "(미작성)"])
        
        csv_data.append(["[4. 약소국 관점 이해하기]", ""])
        wk_list = [r for r in ans.get("weak_df", []) if r.get("주목해 볼 국가") or r.get("그 이유")]
        if wk_list:
            for row in wk_list: 
                csv_data.append([row.get("주목해 볼 국가", ""), f"이유: {row.get('그 이유', '')}"])
        else:
            csv_data.append(["주목해 볼 국가", "(미작성)"])
            csv_data.append(["그 이유", "(미작성)"])

        # 4. 목표로 하는 세계관 (대제목으로 분류되어 파란 밑줄과 함께 질문/답변 박스가 출력됨)
        csv_data.extend([
            ["", ""],
            ["[4. 목표로 하는 세계관]", ""],
            ["▶ 어떤 사람이 되고 싶은가?", ans.get("goal_1", "").strip() or "(미작성)"],
            ["▶ 어떤 세계관을 갖고 싶은가?", ans.get("goal_2", "").strip() or "(미작성)"]
        ])
    # 2학년 수행평가 1 (도시 밈 해석)
    # ----------------------------------------------------
    elif selected_view == ACT_2_1:
        csv_data.extend([["[모둠 구성원]", ""], ["모둠 구성원", f"1: {ans.get('m1_id','')} {ans.get('m1_name','')} / 2: {ans.get('m2_id','')} {ans.get('m2_name','')} / 3: {ans.get('m3_id','')} {ans.get('m3_name','')} / 4: {ans.get('m4_id','')} {ans.get('m4_name','')}"]])
        csv_data.append(["[Step 1. 우리 지역에 대한 '밈' 수집 및 지리 정보 팩트 체크 일지]", ""])
        csv_data.extend([
            ["1. 우리가 선택한 우리 지역의 밈", ans.get("step1_1", "")],
            ["2. 이 밈이 대중에게 심어준 주관적 이미지", ans.get("step1_2", "")],
            ["3. 왜 그런 '밈'이 생기게 되었을까? (주관적 생각)", ans.get("step1_new1", "")],
            ["4. 해당 '밈'이 생기게 된 이유를 지리적 관점에서 생각해 본다면?", ans.get("step1_new2", "")],
            ["5. 우리 모둠에게 특별한 장소감을 주는 장소", ans.get("step1_3", "")],
            ["6. 그 장소에서 느끼는 감정이나 생각", ans.get("step1_4", "")]
        ])
        csv_data.append(["[Step 2. 도시 발달 과정과 객관적 지표]", ""])
        csv_data.extend([
            ["1. 우리 모둠이 탐구할 시기", ans.get("step2_1_period", "")],
            ["2-1. 선택한 시기의 핵심 공간", ans.get("step2_1_space", "")],
            ["2-2. 객관적 특징", ans.get("step2_1_feat", "")],
            ["3. 선택한 시기의 객관적 지리 데이터 혹은 지표", ans.get("step2_3", "")]
        ])
        csv_data.append(["[Step 3. 살기 좋은 울산의 조건: 거주 적합성 진단]", ""])
        for row in ans.get("step3_df", []):
            csv_data.append([row.get("거주 적합성 요인", ""), f"만족도 점수: {row.get('만족도 점수', '')} / 한 줄 평가: {row.get('한 줄 평가', '')}"])
        
        csv_data.append(["[Step 4. 우리의 방식으로 해 보는 울산 브랜딩: 정체성 리뉴얼]", ""])
        csv_data.extend([
            ["1. 기존 프레임(대중의 오해)", ans.get("step4_1", "")],
            ["2. 우리 모둠이 도출한 지리적 본질", ans.get("step4_2", "")],
            ["3. 우리 모둠의 반전 광고 슬로건", ans.get("step4_3", "")],
            ["4. 우리 모둠이 제안하는 울산의 거주 적합성 개선 아이디어", ans.get("step4_4", "")]
        ])

    # ----------------------------------------------------
    # 2학년 수행평가 2 (15분 도시설계)
    # ----------------------------------------------------
    elif selected_view == ACT_2_2:
        csv_data.extend([["[모둠 구성원]", ""], ["모둠 구성원", f"1: {ans.get('m1_id','')} {ans.get('m1_name','')} / 2: {ans.get('m2_id','')} {ans.get('m2_name','')} / 3: {ans.get('m3_id','')} {ans.get('m3_name','')} / 4: {ans.get('m4_id','')} {ans.get('m4_name','')}"]])
        csv_data.append(["[Step 1. 우리 동네 현황 진단]", ""])
        n_min_val = ans.get("step1_n_min", "15분")
        csv_data.append(["▶ 모둠 설정 기준 시간", f"도보 {n_min_val} 도시"])
        csv_data.append(["1. 대상 지역 (예: 학교 주변 인근 OO아파트 OO단지 일대)", ans.get("step1_1", "")])
        for row in ans.get("step1_2_df", []):
            if row.get("구분") or row.get("필수 서비스 항목"):
                stat = "충분" if row.get("충분") else ("부족/없음" if row.get("부족 or 없음") else "")
                csv_data.append([f"[{row.get('구분','')}] {row.get('필수 서비스 항목','')}", stat])
        
        p1 = ans.get('step1_p1', ans.get('step1_3_1', ''))
        d1 = ans.get('step1_d1', '')
        p2 = ans.get('step1_p2', ans.get('step1_3_2', ''))
        d2 = ans.get('step1_d2', '')
        p3 = ans.get('step1_p3', ans.get('step1_3_3', ''))
        d3 = ans.get('step1_d3', '')
        csv_data.extend([
            ["3. 핵심 문제점 1", p1], ["3. 문제점 1 관련 데이터", d1],
            ["3. 핵심 문제점 2", p2], ["3. 문제점 2 관련 데이터", d2],
            ["3. 핵심 문제점 3", p3], ["3. 문제점 3 관련 데이터", d3]
        ])
        
        # Step 2. 도시 개조 트레이드오프 설계표 (새 열 이름 완벽 연동)
        csv_data.append(["[Step 2. 도시 개조 트레이드오프 설계표]", ""])
        s2_rows = ans.get("step2_tradeoff_df") or ans.get("step2_table_df") or ans.get("step2_custom_df", [])
        has_s2_data = False
        for row in s2_rows:
            c_code = str(row.get("선택 코드") or row.get("코드") or "").strip()
            if c_code == "None": c_code = ""
            c_infra = str(row.get("버릴 공간 -> 채울 인프라") or row.get("세부 개조 항목") or "").strip()
            if c_infra == "None": c_infra = ""
            c_pt = str(row.get("사용 포인트") or row.get("비용") or "").strip()
            if c_pt == "None": c_pt = ""
            c_eff = str(row.get("공간 재설계 이유 및 기대효과") or "").strip()
            if c_eff == "None": c_eff = ""
            c_num = str(row.get("순번") or "").strip()

            if c_code or c_infra or c_eff:
                has_s2_data = True
                label = f"▶ [{c_num}번] {c_code} ({c_pt})" if c_code else f"▶ [{c_num}번] 개조 항목"
                detail = f"<b>인프라:</b> {c_infra or '(미작성)'}<br><b>이유 및 기대효과:</b> {c_eff or '(미작성)'}"
                csv_data.append([label, detail])
        if not has_s2_data:
            csv_data.append(["설계표 내용", "(작성된 설계 내용이 없습니다)"])

        # Step 3. N분 도시 공간 지도 스케치 (교사용 화면에 전/후 사진 출력)
        csv_data.append(["[Step 3. N분 도시 공간 지도 스케치]", ""])
        b_img = ans.get("step3_map_before", "")
        a_img = ans.get("step3_map_after", "")
        if b_img or a_img:
            b_tag = f"<img src='{b_img}' style='max-width:320px; max-height:220px; border-radius:6px; border:1px solid #cbd5e1;'>" if b_img else "<span style='color:#94a3b8;'>(미등록)</span>"
            a_tag = f"<img src='{a_img}' style='max-width:320px; max-height:220px; border-radius:6px; border:1px solid #cbd5e1;'>" if a_img else "<span style='color:#94a3b8;'>(미등록)</span>"
            display_html = f"<div style='display:flex; gap:16px; margin-top:8px;'><div><b>[변경 전 지도 스케치]</b><br>{b_tag}</div><div><b>[변경 후 지도 스케치]</b><br>{a_tag}</div></div>"
            csv_data.append(["지도 스케치 (변경 전 / 변경 후)", display_html])
        else:
            csv_data.append(["지도 스케치", "(등록된 지도 스케치 이미지가 없습니다)"])
        
        csv_data.append(["[Step 4. 3분 공청회 발표를 위한 준비]", ""])
        csv_data.extend([
            ["1. 핵심 정책 슬로건", ans.get("step4_1", "")],
            ["2. 실제 답사 및 데이터로 확인한 선택한 지역의 가장 심각한 공간 문제는 무엇이라고 생각하는가?", ans.get("step4_2", "")],
            ["3. 한정된 150pt를 활용해 무엇을 버리고 무엇을 채웠는가? 그 이유는 무엇인가?", ans.get("step4_3", "")],
            ["4. 공간 재설계로 인해 일상이 어떻게 변화할 것이라고 생각하는가?", ans.get("step4_4", "")]
        ])

    # ----------------------------------------------------
    # 2학년 수행평가 3 (미디어 파사드)
    # ----------------------------------------------------
    elif selected_view == ACT_2_3:
        csv_data.extend([["[개별 정보]", ""], ["학번/이름", f"{ans.get('ind_id', '')} / {ans.get('ind_name', '')}"], ["희망 진로", ans.get("ind_career", "")]])
        csv_data.append(["[Step 1. 우리 지역의 정체성 탐색]", ""])
        for row in ans.get("step1_df", []):
            csv_data.append([f"[{row.get('구분', '')}] 키워드", f"{row.get('내가 찾은 정체성 키워드 혹은 문장', '')} (근거: {row.get('근거가 되는 사실·통계·사건', '')} / 출처: {row.get('출처 (기관명 / 자료명 / 연도)', '')})"])
        csv_data.extend([["▶ 최종 선택 키워드", ans.get("step1_keyword", "")], ["▶ 단 하나의 메시지", ans.get("step1_message", "")]])
        
        csv_data.append(["[Step 2. 캔버스 선정]", ""])
        for row in ans.get("step2_matrix_df", []):
            csv_data.append([f"[검토] {row.get('검토 항목', '')}", f"후보1: {row.get('후보 1', '')} | 후보2: {row.get('후보 2', '')} | 후보3: {row.get('후보 3', '')}"])
        csv_data.extend([["▶ 최종 선정 건물", ans.get("step2_final_building", "")], ["▶ 이유", ans.get("step2_reason", "")]])

        csv_data.append(["[Step 3. 주어진 조건 진단 및 대응 설계]", ""])
        for row in ans.get("step3_df", []):
            csv_data.append([row.get("조건 영역", ""), f"실제조건: {row.get('현장의 실제 조건 (확인한 사실)', '')} / 영향: {row.get('작품에 미치는 영향', '')} / 대응: {row.get('나의 대응 방안', '')}"])

        csv_data.append(["[Step 4. 작품 스토리보드 4컷 및 시뮬레이션 영상]", ""])
        s4_rows = ans.get("step4_df", [])
        if s4_rows:
            for row in s4_rows:
                c_name = row.get("컷", "")
                c_desc = row.get("장면 설명 · 사용 기술 · 소요 시간", "") or "(설명 미작성)"
                c_img = row.get("스케치_img", "")
                if c_img:
                    html_display = f"{c_desc}<br><div style='margin-top:8px;'><img src='{c_img}' style='max-width:300px; border-radius:6px; border:1px solid #cbd5e1;'></div>"
                    csv_data.append([f"[{c_name}] 스케치 및 설명", html_display])
                else:
                    csv_data.append([f"[{c_name}] 설명", c_desc])
        else:
            csv_data.append(["스토리보드", "(미작성)"])

        # 최종 시뮬레이션 영상 출력
        sim_video = media_datauri(ans.get("step4_video", ""))
        if sim_video:
            if "data:video" in sim_video:
                v_tag = f"<video src='{sim_video}' controls style='max-width:450px; border-radius:8px; border:1px solid #94a3b8;'></video>"
            else:
                v_tag = f"<img src='{sim_video}' style='max-width:450px; border-radius:8px; border:1px solid #94a3b8; box-shadow:0 4px 6px rgba(0,0,0,0.1);'>"
            csv_data.append(["[최종] 미디어 파사드 시뮬레이션 영상", v_tag])
        else:
            csv_data.append(["[최종] 시뮬레이션 영상", "(영상 미등록)"])
        if str(ans.get("step4_video_link", "")).startswith("http"):
            _lk = __import__("html").escape(ans["step4_video_link"], quote=True)
            csv_data.append(["[최종] 직접 만든 영상 링크", f"<a href='{_lk}' target='_blank'>{_lk}</a>"])
            
        csv_data.append(["[Step 5. 작품 설명 카드 작성 및 갤러리 워크]", ""])
        csv_data.extend([
            ["▶ 작품 제목", ans.get("step5_title", "")],
            ["▶ 전시 장소", ans.get("step5_place", "")],
            ["▶ 작품 개요", ans.get("step5_desc", "")],
            ["▶ 이 작품이 지역의 어떤 정체성을 담았는가", ans.get("step5_identity", ans.get("step5_q1", ""))],
            ["▶ 현장 조건을 어떻게 작품에 반영했는가", ans.get("step5_condition", ans.get("step5_q2", ""))],
            [
            "▶ 이 작품이 우리 지역에 남길 변화",
            (
                f"<div style='line-height: 1.8;'>"
                f"<b>👥 관람객:</b> {ans.get('step5_change_visitor', '').strip() or '(미작성)'}<br>"
                f"<b>🏡 주 민:</b> {ans.get('step5_change_resident', '').strip() or '(미작성)'}<br>"
                f"<b>🛍️ 상 권:</b> {ans.get('step5_change_market', '').strip() or '(미작성)'}"
                f"</div>"
            )
        ]
        ])

        csv_data.append(["[Step 6. 제출 전 자기 점검 및 활용 기록]", ""])
        for row in ans.get("step6_chk_df", []):
            csv_data.append([f"[자기점검] {row.get('점검 항목', '')}", "확인됨" if row.get("확인") else "미확인"])
        for row in ans.get("step6_ai_df", []):
            csv_data.append([f"[AI활용] {row.get('사용한 도구명', '')}", f"프롬프트: {row.get('입력한 프롬프트', '')} / 수정내용: {row.get('AI 결과물을 내가 수정·판단한 내용', '')}"])
        csv_data.append(["▶ 활동 성찰", ans.get("step6_reflection", "")])

    # ----------------------------------------------------
    # 기타 커스텀 문항
    # ----------------------------------------------------
    else:
        for q in (config.get("custom_forms", {}).get(selected_view, []) if config else []):
            csv_data.append([q.get("label", ""), ans.get(q.get("id", ""), "")])

    return csv_data
def generate_html_content(act_name, ans, config=None):
    if ans is None:
        ans = {}
    html = ""
    
    # ----------------------------------------------------
    # 3학년 여행지리 수행평가 1
    # ----------------------------------------------------
    if act_name == ACT_3_1:
        # [Part 1. 영상 기본 정보]
        html += "<h3>Part 1. 영상 기본 정보</h3>"
        html += f"<p><b>1. 영상의 제목:</b> {ans.get('a1_1', '')}</p>"
        html += f"<p><b>2. 영상에 등장하는 국가/지역:</b> {ans.get('a1_2', '')}</p>"
        html += f"<p><b>3. 영상을 선택하게 된 이유:</b></p><div class='content-box'>{ans.get('a1_3', '')}</div>"

        # [Part 2. 영상 감상 및 분석]
        html += "<h3 style='margin-top:20px;'>Part 2. 영상 감상 및 분석</h3>"
        p2_items = [
            ("1. 첫 느낌", ans.get("a2_1", "")),
            ("▶ 인상적이었던 장소/공간", ans.get("a2_2_1", "")),  # 👈 a2_2_1로 수정
            ("▶ 이유", ans.get("a2_2_2", "")),                    # 👈 a2_2_2로 수정
            ("▶ 누구에게 추천", ans.get("a2_3_1", "")),            # 👈 a2_3_1로 수정
            ("▶ 추천하는 이유", ans.get("a2_3_2", "")),          # 👈 a2_3_2로 수정
            ("4. 나만의 감상평", ans.get("a2_4", ""))              # 👈 a2_4로 수정
        ]
        for title, val in p2_items:
            c = str(val).strip().replace("\n", "<br>") if str(val).strip() else "<span style='color:#94a3b8;'>(미작성)</span>"
            html += f"<p style='font-weight:bold; margin-top:10px; margin-bottom:4px;'>{title}</p><div class='content-box' style='background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:8px 12px; margin-bottom:10px;'>{c}</div>"

        # [Part 3. 나만의 여행 영상 기획]
        html += "<h3>Part 3. 나만의 여행 영상 기획</h3>"
        part3_items = [
            ("1) 영상의 제목", ans.get("a3_1", "")),
            ("2) 주요 컨셉/느낌", ans.get("a3_2", "")),
            ("3) 누구와 함께 가고 싶은가?", ans.get("a3_3", "")),
            ("4) 그 이유는?", ans.get("a3_4", "")),
            ("5) 가장 해 보고 싶은 것", ans.get("a3_5", "")),
            ("6) 그 이유는?", ans.get("a3_6", "")),
            ("7) 꼭 넣고 싶은 장소/공간", ans.get("a3_7", "")),
            ("8) 그 이유는?", ans.get("a3_8", "")),
            ("9) 썸네일 영상 기획", ans.get("a3_9", "")),
            ("10) 어울리는 BGM", ans.get("a3_10", "")),
            ("11) 그 이유는?", ans.get("a3_11", ""))
        ]
        for title, val in part3_items:
            c = str(val).strip().replace("\n", "<br>") if str(val).strip() else "<span style='color:#94a3b8;'>(미작성)</span>"
            html += f"<p><b>{title}:</b></p><div class='content-box'>{c}</div>"

    # ----------------------------------------------------
    # 3학년 여행지리 수행평가 2
    # ----------------------------------------------------
    elif act_name == ACT_3_2:
        # [3학년 수행평가 2 - 나를 성장시킨 장소 지도 만들기 18개 문항 HTML 출력]
        q_items = [
            ("1-1) 나에게 편안함을 주는 장소가 있는가?", ans.get("q1_1", "")),
            ("1-2) 어떤 면에서 편안함을 주는가?", ans.get("q1_2", "")),
            ("2-1) 자신의 성격은?", ans.get("q2_1", "")),
            ("2-2) 성격 형성에 영향을 준 장소가 있는가?", ans.get("q2_2", "")),
            ("2-3) 그 이유는 무엇 때문인가?", ans.get("q2_3", "")),
            ("3-1) 자신의 장점은?", ans.get("q3_1", "")),
            ("3-2) 장점 형성에 영향을 준 장소가 있는가?", ans.get("q3_2", "")),
            ("3-3) 그 이유는 무엇 때문인가?", ans.get("q3_3", "")),
            ("4-1) 성장함에 있어 영향을 준 장소가 있는가?", ans.get("q4_1", "")),
            ("4-2) 어떤 면에서 영향을 주었는가?", ans.get("q4_2", "")),
            ("5-1) 지금 나의 목표는?", ans.get("q5_1", "")),
            ("5-2) 목표 설정에 영향을 준 장소가 있는가?", ans.get("q5_2", "")),
            ("6-1) 소중한 사람에게 소개해 주고 싶은 장소가 있는가?", ans.get("q6_1", "")),
            ("6-2) 그 이유는?", ans.get("q6_2", "")),
            ("7-1) 나만의 비밀 장소가 있는가?", ans.get("q7_1", "")),
            ("7-2) 그 이유는?", ans.get("q7_2", "")),
            ("8-1) 과거로 돌아갈 수 있다면 다시 가고 싶은 장소는?", ans.get("q8_1", "")),
            ("8-2) 그 이유는?", ans.get("q8_2", ""))
        ]

        for title, val in q_items:
            content = str(val).strip().replace("\n", "<br>") if str(val).strip() else "<span style='color:#94a3b8;'>(미작성)</span>"
            html += f"<p style='font-weight:bold; margin-top:14px; margin-bottom:4px; color:#1e293b; font-size:14px;'>{title}</p>"
            html += f"<div class='content-box' style='background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:10px 14px; line-height:1.6; font-size:13.5px;'>{content}</div>"

    # ----------------------------------------------------
    # 3학년 여행지리 수행평가 3
    # ----------------------------------------------------
    elif act_name == ACT_3_3:
        html += "<h3>1. 세계 인식 수준에 대한 확인</h3>"
        html += "<h4>1. 대륙별 관심도 및 지식 수준 체크</h4><table><tr><th>대륙</th><th>관심도</th><th>지식수준</th></tr>"
        for row in ans.get("s1_df", []):
            html += f"<tr><td>{row.get('대륙','')}</td><td>{row.get('관심도','')}</td><td>{row.get('지식수준','')}</td></tr>"
        html += "</table>"
        
        html += "<h4>2. (개인적 경험에 기반) 특정 국가에 대한 기억과 인상에 대한 분석</h4>"
        html += "<h5>[직접 경험]</h5><table><tr><th>여행해 본 국가</th><th>해당 국가에 대한 구체적인 기억 혹은 인상</th></tr>"
        for row in ans.get("direct_df", []):
            html += f"<tr><td>{row.get('여행해 본 국가','')}</td><td>{row.get('해당 국가에 대한 구체적인 기억 혹은 인상','')}</td></tr>"
        html += "</table>"
        
        html += f"<h5>[간접 경험]</h5><ul>"
        html += f"<li>즐겨 보는 외국 영화/드라마는 어느 나라 작품?: {ans.get('ind1','')}</li>"
        html += f"<li>좋아하는 음악가나 연예인이 있다면 어느 나라?: {ans.get('ind2','')}</li>"
        html += f"<li>자주 먹는 외국 음식이 있다면 어느 나라?: {ans.get('ind3','')}</li></ul>"
        
        html += "<h4>3. 꼭 가보고 싶은 Top 5 국가와 그 이유</h4><table><tr><th>국가 혹은 지역</th><th>이유</th></tr>"
        for row in ans.get("top5_want", []):
            html += f"<tr><td>{row.get('국가 혹은 지역','')}</td><td>{row.get('이유','')}</td></tr>"
        html += "</table>"
        
        html += "<h4>4. 절대 가고 싶지 않은 Top 5 국가와 그 이유</h4><table><tr><th>국가 혹은 지역</th><th>이유</th></tr>"
        for row in ans.get("top5_notwant", []):
            html += f"<tr><td>{row.get('국가 혹은 지역','')}</td><td>{row.get('이유','')}</td></tr>"
        html += "</table>"

        html += "<h3>2. 특정 대륙/국가에 대한 자신의 편견과 고정관념</h3>"
        html += "<h4>1. 국가별 한 단어 라벨링</h4><table><tr><th>가 보고 싶은 국가</th><th>한 단어 라벨</th><th>가고 싶지 않은 국가</th><th>한 단어 라벨</th></tr>"
        for row in ans.get("label_df", []):
            html += f"<tr><td>{row.get('가 보고 싶은 국가','')}</td><td>{row.get('한 단어 라벨','')}</td><td>{row.get('가고 싶지 않은 국가','')}</td><td>{row.get('한 단어 라벨(부정)','')}</td></tr>"
        html += "</table>"

        html += "<h4>2. 개인적으로 가장 강한 편견을 가진 국가</h4><table><tr><th>국가명</th><th>편견 내용</th><th>편견 형성 과정 혹은 이유</th></tr>"
        for row in ans.get("prej_df", []):
            html += f"<tr><td>{row.get('국가명','')}</td><td>{row.get('편견 내용','')}</td><td>{row.get('편견 형성 과정 혹은 이유','')}</td></tr>"
        html += "</table>"

        html += "<h4>3. 미디어와 교육의 영향으로 인한 인식 발견</h4><table>"
        html += f"<tr><th>뉴스에서 자주 접하는 국가들</th><td>{ans.get('media1_1','')}</td><th>그 나라들에 대한 이미지</th><td>{ans.get('media1_2','')}</td></tr>"
        html += f"<tr><th>영화/드라마에서 자주 접하는 국가들</th><td>{ans.get('media2_1','')}</td><th>그 나라들에 대한 이미지</th><td>{ans.get('media2_2','')}</td></tr>"
        html += f"<tr><th>학교에서 많이 배운 국가들</th><td>{ans.get('media3_1','')}</td><th>그 나라들에 대한 지식</th><td>{ans.get('media3_2','')}</td></tr></table>"

        html += "<h4>4. 부정확한 정보나 과장된 인식 발견</h4><table><tr><th>국가명</th><th>잘못 알고 있었던 내용</th><th>실제 사실</th></tr>"
        for row in ans.get("fake_df", []):
            html += f"<tr><td>{row.get('국가명','')}</td><td>{row.get('잘못 알고 있었던 내용','')}</td><td>{row.get('실제 사실','')}</td></tr>"
        html += "</table>"

        html += "<h4>5. 우월감이나 차별 의식 점검</h4><table><tr><th>어떤 국가에 대해?</th><th>어떤 측면에서</th><th>우월감이나 차별 의식을 느끼는 이유</th></tr>"
        for row in ans.get("discrim_df", []):
            html += f"<tr><td>{row.get('어떤 국가에 대해?','')}</td><td>{row.get('어떤 측면에서','')}</td><td>{row.get('그 이유','')}</td></tr>"
        html += "</table>"

        html += "<h3>3. 포용적이고 균형잡힌 세계관을 위한 노력</h3>"
        html += "<h4>1. 편견을 바꾸고 싶은 국가</h4><table><tr><th>어떤 국가에 대해?</th><th>현재의 편견</th><th>올바른 정보를 찾기 위한 계획</th></tr>"
        for row in ans.get("change_df", []):
            html += f"<tr><td>{row.get('어떤 국가에 대해?','')}</td><td>{row.get('현재의 편견','')}</td><td>{row.get('올바른 정보를 찾기 위한 계획','')}</td></tr>"
        html += "</table>"

        html += "<h4>2. 가장 무관심했던 대륙 혹은 국가</h4><table><tr><th>선택 대륙/국가</th><th>무관심 이유</th><th>관심 확장을 위한 정보 수집 방법</th></tr>"
        for row in ans.get("ignore_df", []):
            html += f"<tr><td>{row.get('선택 대륙/국가','')}</td><td>{row.get('무관심 이유','')}</td><td>{row.get('관심 확장을 위한 정보 수집 방법','')}</td></tr>"
        html += "</table>"

        html += "<h4>3. 서구 중심적 시각에서 벗어나기</h4><table><tr><th>현재 가지고 있는 서구 중심적 시각</th><th>개선 방법</th></tr>"
        for row in ans.get("western_df", []):
            html += f"<tr><td>{row.get('현재 가지고 있는 서구 중심적 시각','')}</td><td>{row.get('개선 방법','')}</td></tr>"
        html += "</table>"

        html += "<h4>4. 약소국 관점 이해하기</h4><table><tr><th>주목해 볼 국가</th><th>그 이유</th></tr>"
        for row in ans.get("weak_df", []):
            html += f"<tr><td>{row.get('주목해 볼 국가','')}</td><td>{row.get('그 이유','')}</td></tr>"
        html += "</table>"

        html += "<h3>4. 목표로 하는 세계관</h3>"
        html += f"<p><b>▶ 어떤 사람이 되고 싶은가?</b></p><div class='content-box'>{ans.get('goal_1','')}</div>"
        html += f"<p><b>▶ 어떤 세계관을 갖고 싶은가?</b></p><div class='content-box'>{ans.get('goal_2','')}</div>"

    # ----------------------------------------------------
    # 2학년 수행평가 1 (도시 밈 해석)
    # ----------------------------------------------------
    elif act_name == ACT_2_1:
        html += f"<h4>모둠 구성원</h4><p>1: {ans.get('m1_id','')} {ans.get('m1_name','')} / 2: {ans.get('m2_id','')} {ans.get('m2_name','')} / 3: {ans.get('m3_id','')} {ans.get('m3_name','')} / 4: {ans.get('m4_id','')} {ans.get('m4_name','')}</p>"
        html += "<h3>Step 1. 우리 지역에 대한 '밈' 수집 및 지리 정보 팩트 체크 일지</h3>"
        html += f"<p><b>1. 우리가 선택한 우리 지역의 밈:</b><br>{ans.get('step1_1','')}</p>"
        html += f"<p><b>2. 이 밈이 대중에게 심어준 주관적 이미지:</b><br>{ans.get('step1_2','')}</p>"
        html += f"<p><b>3. 왜 그런 '밈'이 생기게 되었을까? (주관적 생각):</b><br>{ans.get('step1_new1','')}</p>"
        html += f"<p><b>4. 해당 '밈'이 생기게 된 이유를 지리적 관점에서 생각해 본다면?:</b><br>{ans.get('step1_new2','')}</p>"
        html += f"<p><b>5. 우리 모둠에게 특별한 장소감을 주는 장소:</b><br>{ans.get('step1_3','')}</p>"
        html += f"<p><b>6. 그 장소에서 느끼는 감정이나 생각:</b><br>{ans.get('step1_4','')}</p>"
        
        html += "<h3>Step 2. 도시 발달 과정과 객관적 지표</h3>"
        html += f"<p><b>1. 우리 모둠이 탐구할 시기:</b> {ans.get('step2_1_period','')}</p>"
        html += f"<p><b>2-1. 선택한 시기의 핵심 공간:</b><br>{ans.get('step2_1_space','')}</p>"
        html += f"<p><b>2-2. 객관적 특징:</b><br>{ans.get('step2_1_feat','')}</p>"
        html += f"<p><b>3. 선택한 시기의 객관적 지리 데이터 혹은 지표:</b><br>{ans.get('step2_3','')}</p>"

        html += "<h3>Step 3. 살기 좋은 울산의 조건: 거주 적합성 진단</h3>"
        html += "<table><tr><th>거주 적합성 요인</th><th>만족도 점수</th><th>한 줄 평가</th></tr>"
        for row in ans.get("step3_df", []):
            html += f"<tr><td>{row.get('거주 적합성 요인','')}</td><td>{row.get('만족도 점수','')}</td><td>{row.get('한 줄 평가','')}</td></tr>"
        html += "</table>"

        html += "<h3>Step 4. 우리의 방식으로 해 보는 울산 브랜딩: 정체성 리뉴얼</h3>"
        html += f"<p><b>1. 기존 프레임(대중의 오해):</b><br>{ans.get('step4_1','')}</p>"
        html += f"<p><b>2. 우리 모둠이 도출한 지리적 본질:</b><br>{ans.get('step4_2','')}</p>"
        html += f"<p><b>3. 우리 모둠의 반전 광고 슬로건:</b><br>{ans.get('step4_3','')}</p>"
        html += f"<p><b>4. 우리 모둠이 제안하는 울산의 거주 적합성 개선 아이디어:</b><br>{ans.get('step4_4','')}</p>"

    # ----------------------------------------------------
    # 2학년 수행평가 2 (15분 도시설계)
    # ----------------------------------------------------
    elif act_name == ACT_2_2:
        html += f"<h4>모둠 구성원</h4><p>1: {ans.get('m1_id','')} {ans.get('m1_name','')} / 2: {ans.get('m2_id','')} {ans.get('m2_name','')} / 3: {ans.get('m3_id','')} {ans.get('m3_name','')} / 4: {ans.get('m4_id','')} {ans.get('m4_name','')}</p>"
        n_min_val = ans.get("step1_n_min", "15분")
        html += "<h3>Step 1. 우리 동네 현황 진단</h3>"
        html += f"<p><b>▶ 모둠 설정 기준 시간:</b> 도보 {n_min_val} 도시</p>"
        html += f"<p><b>1. 대상 지역:</b> {ans.get('step1_1','')}</p>"
        html += f"<h4>2. {n_min_val} 생활권 반경 내 필수 서비스 체크리스트</h4>"
        html += "<table><tr><th>구분</th><th>필수 서비스 항목</th><th>충분</th><th>부족 or 없음</th></tr>"
        for row in ans.get("step1_2_df", []):
            if row.get("구분") or row.get("필수 서비스 항목"):
                c_ok = "V" if row.get("충분") else ""
                c_no = "V" if row.get("부족 or 없음") else ""
                html += f"<tr><td>{row.get('구분','')}</td><td>{row.get('필수 서비스 항목','')}</td><td style='text-align:center;'>{c_ok}</td><td style='text-align:center;'>{c_no}</td></tr>"
        html += "</table>"
        
        html += "<h4>3. 선택한 지역의 핵심 문제점</h4>"
        html += "<table><tr><th style='width:50%;'>문제점</th><th style='width:50%;'>데이터</th></tr>"
        p1 = ans.get('step1_p1', ans.get('step1_3_1', ''))
        d1 = ans.get('step1_d1', '')
        p2 = ans.get('step1_p2', ans.get('step1_3_2', ''))
        d2 = ans.get('step1_d2', '')
        p3 = ans.get('step1_p3', ans.get('step1_3_3', ''))
        d3 = ans.get('step1_d3', '')
        html += f"<tr><td><b>문제점 1:</b><br>{p1}</td><td><b>데이터 1:</b><br>{d1}</td></tr>"
        html += f"<tr><td><b>문제점 2:</b><br>{p2}</td><td><b>데이터 2:</b><br>{d2}</td></tr>"
        html += f"<tr><td><b>문제점 3:</b><br>{p3}</td><td><b>데이터 3:</b><br>{d3}</td></tr>"
        html += "</table>"

        # Step 2. 도시 개조 트레이드오프 설계표 HTML
        html += "<h3>Step 2. 도시 개조 트레이드오프 설계표</h3>"
        t_rows = ans.get("step2_tradeoff_df") or ans.get("step2_table_df") or ans.get("step2_custom_df", [])
        if t_rows:
            html += "<table style='width:100%; border-collapse:collapse; text-align:center;'><tr><th style='width:8%;'>순번</th><th style='width:14%;'>선택 코드</th><th style='width:38%;'>버릴 공간 -> 채울 인프라</th><th style='width:12%;'>사용 포인트</th><th style='width:28%;'>공간 재설계 이유 및 기대효과</th></tr>"
            for r in t_rows:
                c_code = str(r.get("선택 코드") or r.get("코드") or "").strip()
                if c_code == "None": c_code = ""
                c_infra = str(r.get("버릴 공간 -> 채울 인프라") or r.get("세부 개조 항목") or "").strip()
                if c_infra == "None": c_infra = ""
                c_pt = str(r.get("사용 포인트") or r.get("비용") or "").strip()
                if c_pt == "None": c_pt = ""
                c_eff = str(r.get("공간 재설계 이유 및 기대효과") or "").strip()
                if c_eff == "None": c_eff = ""
                if c_code or c_infra or c_eff:
                    html += f"<tr><td>{r.get('순번','')}</td><td><b>{c_code}</b></td><td style='text-align:left;'>{c_infra}</td><td>{c_pt}</td><td style='text-align:left;'>{c_eff}</td></tr>"
            html += "</table>"

        # Step 3. N분 도시 공간 지도 스케치 HTML
        html += "<h3>Step 3. N분 도시 공간 지도 스케치</h3>"
        b_img = ans.get("step3_map_before", "")
        a_img = ans.get("step3_map_after", "")
        b_html = f"<img src='{b_img}' style='max-width:100%; max-height:350px; border-radius:6px; border:1px solid #cbd5e1; display:block; margin:0 auto;'>" if b_img else "<p style='color:#94a3b8;'>(미등록)</p>"
        a_html = f"<img src='{a_img}' style='max-width:100%; max-height:350px; border-radius:6px; border:1px solid #cbd5e1; display:block; margin:0 auto;'>" if a_img else "<p style='color:#94a3b8;'>(미등록)</p>"

        html += f"""
        <table style="width:100%; border-collapse:collapse; margin-top:10px;">
            <tr style="background:#f8fafc; text-align:center;">
                <th style="width:50%; padding:10px; border:1px solid #cbd5e1;">[전] 변경 전 지도 스케치</th>
                <th style="width:50%; padding:10px; border:1px solid #cbd5e1;">[후] 변경 후 지도 스케치</th>
            </tr>
            <tr>
                <td style="text-align:center; padding:15px; border:1px solid #cbd5e1; vertical-align:middle;">{b_html}</td>
                <td style="text-align:center; padding:15px; border:1px solid #cbd5e1; vertical-align:middle;">{a_html}</td>
            </tr>
        </table>
        """

        html += "<h3>Step 4. 3분 공청회 발표를 위한 준비</h3>"
        html += f"<p><b>1. 핵심 정책 슬로건:</b></p><div class='content-box'>{ans.get('step4_1','')}</div>"
        html += "<div style='text-align: center; font-weight: bold; background-color: #eee; padding: 6px; margin: 15px 0;'>연설 내용 구조화 스크립트 작성</div>"
        html += f"<p><b>2. 실제 답사 및 데이터로 확인한 선택한 지역의 가장 심각한 공간 문제는 무엇이라고 생각하는가?:</b></p><div class='content-box'>{ans.get('step4_2','')}</div>"
        html += f"<p><b>3. 한정된 150pt를 활용해 무엇을 버리고 무엇을 채웠는가? 그 이유는 무엇인가?:</b></p><div class='content-box'>{ans.get('step4_3','')}</div>"
        html += f"<p><b>4. 공간 재설계로 인해 일상이 어떻게 변화할 것이라고 생각하는가?:</b></p><div class='content-box'>{ans.get('step4_4','')}</div>"

    # ----------------------------------------------------
    # 2학년 수행평가 3 (미디어 파사드)
    # ----------------------------------------------------
    elif act_name == ACT_2_3:
        html += f"<h4>개별 정보</h4><p>학번/이름: {ans.get('ind_id','')} {ans.get('ind_name','')} | 희망 진로: {ans.get('ind_career','')}</p>"
        
        html += "<h3>Step 1. 우리 지역의 정체성 탐색</h3>"
        html += "<table><tr><th>구분</th><th>내가 찾은 정체성 키워드 혹은 문장</th><th>근거가 되는 사실·통계·사건</th><th>출처 (기관명 / 자료명 / 연도)</th></tr>"
        for row in ans.get("step1_df", []):
            if row.get("내가 찾은 정체성 키워드 혹은 문장") or row.get("구분"):
                html += f"<tr><td>{row.get('구분','')}</td><td>{row.get('내가 찾은 정체성 키워드 혹은 문장','')}</td><td>{row.get('근거가 되는 사실·통계·사건','')}</td><td>{row.get('출처 (기관명 / 자료명 / 연도)','')}</td></tr>"
        html += "</table>"
        html += f"<p><b>▶ 내가 최종 선택한 핵심 키워드 혹은 문장:</b> {ans.get('step1_keyword','')}</p>"
        html += f"<p><b>▶ 내 작품이 전할 단 하나의 메시지:</b></p><div class='content-box'>{ans.get('step1_message','')}</div>"

        html += "<h3>Step 2. 캔버스 선정 — 어떤 건축물에 어떤 형태의 빛을 입힐 것인가?</h3>"
        html += "<table><tr><th>검토 항목</th><th>후보 1</th><th>후보 2</th><th>후보 3</th></tr>"
        for row in ans.get("step2_matrix_df", []):
            html += f"<tr><td>{row.get('검토 항목','')}</td><td>{row.get('후보 1','')}</td><td>{row.get('후보 2','')}</td><td>{row.get('후보 3','')}</td></tr>"
        html += "</table>"
        html += f"<p><b>▶ 최종 선정 건물:</b> {ans.get('step2_final_building','')}</p>"
        html += f"<p><b>▶ 이유:</b></p><div class='content-box'>{ans.get('step2_reason','')}</div>"

        html += "<h3>Step 3. 주어진 조건 진단 및 대응 설계</h3>"
        html += "<table><tr><th>조건 영역</th><th>현장의 실제 조건 (확인한 사실)</th><th>작품에 미치는 영향</th><th>나의 대응 방안</th></tr>"
        for row in ans.get("step3_df", []):
            if row.get("현장의 실제 조건 (확인한 사실)") or row.get("나의 대응 방안"):
                html += f"<tr><td>{row.get('조건 영역','')}</td><td>{row.get('현장의 실제 조건 (확인한 사실)','')}</td><td>{row.get('작품에 미치는 영향','')}</td><td>{row.get('나의 대응 방안','')}</td></tr>"
        html += "</table>"

        html += "<h3>Step 4. 작품 스토리보드 4컷 & 미디어 파사드 시뮬레이션 영상</h3>"
        html += "<table style='width:100%; border-collapse: collapse; margin-top:10px; font-size:14px;'>"
        html += "<tr style='background-color:#f8fafc;'><th style='width:12%; padding:10px; border:1px solid #cbd5e1; text-align:center;'>컷</th><th style='width:38%; padding:10px; border:1px solid #cbd5e1; text-align:center;'>스케치</th><th style='width:50%; padding:10px; border:1px solid #cbd5e1; text-align:center;'>장면 설명 · 사용 기술 · 소요 시간</th></tr>"
        
        for row in ans.get("step4_df", []):
            c_name = row.get("컷", "")
            c_desc = row.get("장면 설명 · 사용 기술 · 소요 시간", "").replace("\n", "<br>")
            c_img = row.get("스케치_img", "")
            
            if c_img:
                img_tag = f"<img src='{c_img}' style='max-width:260px; max-height:180px; border-radius:6px; border:1px solid #e2e8f0; display:block; margin:0 auto;'>"
            else:
                img_tag = "<span style='color:#94a3b8; font-size:13px;'>(스케치 미등록)</span>"
                
            html += f"<tr><td style='text-align:center; font-weight:bold; border:1px solid #cbd5e1; padding:8px; background-color:#fafafa;'>{c_name}</td><td style='text-align:center; border:1px solid #cbd5e1; padding:8px; vertical-align:middle;'>{img_tag}</td><td style='border:1px solid #cbd5e1; padding:10px; vertical-align:top; line-height:1.6;'>{c_desc}</td></tr>"
            
        html += "</table>"

        # 시뮬레이션 영상 HTML 출력
        sim_v = media_datauri(ans.get("step4_video", ""))
        if sim_v:
            html += "<div style='margin-top:15px; padding:15px; background-color:#f8fafc; border-radius:8px; border:1px solid #e2e8f0; text-align:center;'>"
            html += "<p style='font-weight:bold; font-size:15px; margin-bottom:10px; color:#1e293b;'>🎬 미디어 파사드 모션 시뮬레이션</p>"
            if "data:video" in sim_v:
                html += f"<video src='{sim_v}' controls style='max-width:520px; border-radius:6px; box-shadow:0 2px 6px rgba(0,0,0,0.1);'></video>"
            else:
                html += f"<img src='{sim_v}' style='max-width:520px; border-radius:6px; box-shadow:0 2px 6px rgba(0,0,0,0.1);'>"
            html += "</div>"
        if str(ans.get("step4_video_link", "")).startswith("http"):
            _lk = __import__("html").escape(ans["step4_video_link"], quote=True)
            html += f"<p style='margin-top:10px;'>🔗 직접 만든 영상 링크: <a href='{_lk}' target='_blank'>{_lk}</a></p>"

        html += "<h3>Step 5. 작품 설명 카드 작성 및 갤러리 워크</h3>"
        html += f"<p><b>▶ 작품 제목:</b> {ans.get('step5_title','')}</p>"
        html += f"<p><b>▶ 전시 장소:</b> {ans.get('step5_place','')}</p>"
        html += f"<p><b>▶ 작품 개요:</b></p><div class='content-box'>{ans.get('step5_desc','')}</div>"
        html += f"<p><b>▶ 이 작품이 지역의 어떤 정체성을 담았는가:</b></p><div class='content-box'>{ans.get('step5_identity', ans.get('step5_q1',''))}</div>"
        html += f"<p><b>▶ 현장 조건을 어떻게 작품에 반영했는가:</b></p><div class='content-box'>{ans.get('step5_condition', ans.get('step5_q2',''))}</div>"
        vis = ans.get("step5_change_visitor", "").strip()
        res = ans.get("step5_change_resident", "").strip()
        mkt = ans.get("step5_change_market", "").strip()
        if vis or res or mkt:
            change_content = (
                f"<b>• 👥 관람객:</b> {vis or '(미작성)'}<br>"
                f"<b>• 🏡 주 민:</b> {res or '(미작성)'}<br>"
                f"<b>• 🛍️ 상 권:</b> {mkt or '(미작성)'}"
            )
        else:
            change_content = ans.get("step5_change", ans.get("step5_q3", "(미작성)"))
        html += f"<p><b>▶ 이 작품이 우리 지역에 남길 변화:</b></p><div class='content-box'>{change_content}</div>"

        html += "<h3>Step 6. 제출 전 자기 점검 및 활용 기록</h3>"
        html += "<h4>[자기 점검]</h4><table><tr><th>No</th><th>점검 항목</th><th>확인</th></tr>"
        for row in ans.get("step6_chk_df", []):
            chk_str = "V" if row.get("확인") else ""
            html += f"<tr><td style='text-align:center;'>{row.get('No','')}</td><td>{row.get('점검 항목','')}</td><td style='text-align:center;'>{chk_str}</td></tr>"
        html += "</table>"

        html += "<h4>[생성형 AI 활용 기록]</h4><table><tr><th>사용한 도구명</th><th>입력한 프롬프트</th><th>AI 결과물을 내가 수정·판단한 내용</th></tr>"
        for row in ans.get("step6_ai_df", []):
            if row.get("사용한 도구명") or row.get("입력한 프롬프트"):
                html += f"<tr><td>{row.get('사용한 도구명','')}</td><td>{row.get('입력한 프롬프트','')}</td><td>{row.get('AI 결과물을 내가 수정·판단한 내용','')}</td></tr>"
        html += "</table>"
        html += f"<p><b>▶ 활동 성찰:</b></p><div class='content-box'>{ans.get('step6_reflection','')}</div>"

    # ----------------------------------------------------
    # 기타 커스텀 문항 처리
    # ----------------------------------------------------
    else:
        for q in (config.get("custom_forms", {}).get(act_name, []) if config else []):
            html += f"<p><b>{q.get('label', '')}:</b><br>{ans.get(q.get('id', ''), '')}</p>"

    return html

# --- 표의 빈칸(NaN)이 내보내기/시트에 'nan' 으로 찍히던 문제 정리 ---
import math as _math
def _clean_nan(x):
    if isinstance(x, float) and _math.isnan(x): return ""
    if isinstance(x, float) and x.is_integer(): return int(x)   # 1.0번 → 1번
    if isinstance(x, dict): return {k: _clean_nan(v) for k, v in x.items()}
    if isinstance(x, list): return [_clean_nan(v) for v in x]
    return x
# --- 미디어 파사드 표의 항목 이름: 예전에 저장한 자료도 '지금 코드의 이름'으로 맞춤 (앱 화면·구글 시트·내보내기 공통) ---
# ✏️ 항목 이름(괄호 안 설명)을 바꾸고 싶으면 아래 두 목록만 고치면 됩니다. 학생이 적은 내용은 그대로 남습니다.
MF_S2_ITEMS = ["건물명", "벽면 조건\n(면적·재질·창문 비율)", "관람 조건\n(관람 거리, 야간 유동인구)", "접근성\n(대중교통·주차·야간)",
               "예상되는 제약\n(빛공해, 인접한 주거지와의 거리)", "지역 정체성과의 연관성", "적합도 (☆ 5점 만점)"]
MF_S3_DEFAULTS = ["1. 물리적 조건\n(벽면 형태·재질·구조물)", "2. 빛·환경 조건\n(주변 조명·간판·빛공해)",
                  "3. 주민·이웃 조건\n(인접 주거·상가·소음)", "4. 접근·안전 조건\n(동선·차도·배리어프리)"]
MF_S3_REMOVED = ["시간·계절", "행정·비용"]   # 표에서 지운 조건: 화면·시트·점검에서는 빠지지만, 학생이 이미 적은 내용은 저장 자료 안에 숨겨서 보관(지우지 않음)

def _mf_head(t):
    import re as _r
    return _r.split(r"[\n(]", _r.sub(r"^\s*\d+\.\s*", "", str(t or "")))[0].strip()

def mf_normalize_ans(ans):
    """저장된 미디어 파사드 자료의 표 항목 이름을 현재 이름으로 바꾸고, 지운 조건 줄은 뺀다 (원본은 건드리지 않음)"""
    if not isinstance(ans, dict):
        return ans
    import re as _r
    out = dict(ans)
    s2_name = {_mf_head(it): it for it in MF_S2_ITEMS}
    if isinstance(ans.get("step2_matrix_df"), list):
        out["step2_matrix_df"] = [dict(r, **{"검토 항목": s2_name.get(_mf_head(r.get("검토 항목")), r.get("검토 항목", ""))}) if isinstance(r, dict) else r
                                  for r in ans["step2_matrix_df"]]
    if isinstance(ans.get("step3_df"), list):
        s3_name = {_mf_head(it): it for it in MF_S3_DEFAULTS}
        rows = [dict(r) for r in ans["step3_df"] if isinstance(r, dict) and not any(x in str(r.get("조건 영역", "")) for x in MF_S3_REMOVED)]
        for i, r in enumerate(rows):
            lab = str(r.get("조건 영역", ""))
            if _mf_head(lab) in s3_name:          # 지금 목록에 있는 조건 → 지금 이름(번호 포함) 그대로
                r["조건 영역"] = s3_name[_mf_head(lab)]
            elif _r.match(r"^\s*\d+\.", lab):    # 예전 이름(예: 사회적 조건) → 줄 순서대로 번호만 다시 매김
                r["조건 영역"] = _r.sub(r"^\s*\d+\.\s*", f"{i + 1}. ", lab)
        out["step3_df"] = rows
    return out

_get_act_csv_rows_raw = get_act_csv_rows
def get_act_csv_rows(selected_view, ans, config=None):
    ans = _clean_nan(ans)
    if selected_view == ACT_2_3: ans = mf_normalize_ans(ans)
    return _get_act_csv_rows_raw(selected_view, ans, config)
_generate_html_content_raw = generate_html_content
def generate_html_content(act_name, ans, config=None):
    ans = _clean_nan(ans)
    if act_name == ACT_2_3: ans = mf_normalize_ans(ans)
    return _generate_html_content_raw(act_name, ans, config)

def generate_portfolio_html(user_key, u_info, view_subj, config, learning_data):
    u_id = u_info.get('id', '')
    u_name = u_info.get('name', '학생')
    u_class = u_info.get('class_group', '')
    
    html = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>{u_name} 수행평가 포트폴리오</title>
    <style>
        body {{ font-family: 'Malgun Gothic', sans-serif; padding: 40px; line-height: 1.6; color: #333; }}
        h1 {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 20px; }}
        h2 {{ color: #2c3e50; border-left: 5px solid #3498db; padding-left: 10px; margin-top: 40px; }}
        h3 {{ color: #2980b9; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 20px; table-layout: fixed; }}
        th {{ background-color: #ecf0f1; width: 30%; border: 1px solid #bdc3c7; padding: 10px; text-align: left; }}
        td {{ border: 1px solid #bdc3c7; padding: 10px; text-align: left; white-space: pre-wrap; }}
        .content-box {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; border: 1px solid #e9ecef; white-space: pre-wrap; }}
    </style></head><body>
    <h1>📚 {view_subj} 수행평가 포트폴리오</h1>
    <div style="text-align: right; margin-bottom: 30px;"><b>반:</b> {u_class} | <b>이름:</b> {u_name}</div>
    """
    acts_for_subj = config.get("subject_activities", {}).get(view_subj, [])
    for act in acts_for_subj:
        owner_key, ans = get_user_activity_data(user_key, u_id, view_subj, u_class, act, learning_data)
        if not ans: continue
        html += f"<h2>▶ {act}</h2>"
        html += generate_html_content(act, ans, config)
    html += "</body></html>"
    return html

def generate_activity_html(act_name, ans, u_name):
    html = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>{u_name} - {act_name}</title>
    <style>
        body {{ font-family: 'Malgun Gothic', sans-serif; padding: 40px; line-height: 1.6; color: #333; }} 
        h2 {{ color: #2c3e50; border-left: 5px solid #3498db; padding-left: 10px; }} 
        h3 {{ color: #2980b9; }} 
        table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 20px; table-layout: fixed; }} 
        th {{ background-color: #ecf0f1; width: 30%; border: 1px solid #bdc3c7; padding: 10px; text-align: left; }} 
        td {{ border: 1px solid #bdc3c7; padding: 10px; text-align: left; white-space: pre-wrap; }} 
        .content-box {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; border: 1px solid #e9ecef; white-space: pre-wrap; }}
    </style></head><body>
    <div style="text-align: right; margin-bottom: 20px;"><b>이름:</b> {u_name}</div><h2>▶ {act_name}</h2>"""
    html += generate_html_content(act_name, ans)
    html += "</body></html>"
    return html

AUTOSAVE_JS = """<script>
(function(){
  var P = window.parent, doc = P.document;
  var PREFIX = 'asv2___SCOPE___';
  var CLEAR = __CLEAR__;
  var MAX_AGE = 12 * 3600 * 1000;
  function eachKey(fn){ try { var ks = []; for (var i = 0; i < P.localStorage.length; i++) ks.push(P.localStorage.key(i)); ks.forEach(fn); } catch(e) {} }
  // 예전 스크립트가 남긴 키(학생 구분 없이 저장되어 내용이 섞이던 원인) 정리
  eachKey(function(k){ if (k.indexOf('autosave_') === 0) P.localStorage.removeItem(k); });
  // 저장 성공 직후에는 이 화면의 임시 기록을 비움 / 오래된 기록도 정리
  eachKey(function(k){
    if (k.indexOf('asv2_') !== 0) return;
    if (CLEAR && k.indexOf(PREFIX) === 0) { P.localStorage.removeItem(k); return; }
    try { var o = JSON.parse(P.localStorage.getItem(k)); if (!o || Date.now() - o.t > MAX_AGE) P.localStorage.removeItem(k); } catch(e) { P.localStorage.removeItem(k); }
  });
  function setNative(el, v){
    var proto = el.tagName === 'TEXTAREA' ? P.HTMLTextAreaElement.prototype : P.HTMLInputElement.prototype;
    var d = Object.getOwnPropertyDescriptor(proto, 'value');
    if (d && d.set) d.set.call(el, v); else el.value = v;
    el.dispatchEvent(new P.Event('input', { bubbles: true }));
  }
  function attach(){
    var seen = {};
    var els = doc.querySelectorAll('section.main textarea, section.main input[type="text"], [data-testid="stMain"] textarea, [data-testid="stMain"] input[type="text"]');
    var done = new Set();
    els.forEach(function(el){
      if (done.has(el)) return; done.add(el);
      if (el.getAttribute('role') === 'combobox' || el.readOnly) return;
      var label = el.getAttribute('aria-label') || '';
      if (!label) return;
      seen[label] = (seen[label] || 0) + 1;            // 같은 문항 이름이 여러 개여도 칸마다 따로 저장
      el.dataset.asv2 = PREFIX + label + '#' + seen[label];
      if (el.dataset.asv2bound) return;
      el.dataset.asv2bound = '1';
      el.addEventListener('input', function(){
        if (el.disabled || !el.dataset.asv2) return;
        try { P.localStorage.setItem(el.dataset.asv2, JSON.stringify({ v: el.value, t: Date.now() })); } catch(e) {}
      });
      el.addEventListener('focus', function(){
        if (el.disabled || el.value !== '' || !el.dataset.asv2) return;
        var o = null; try { o = JSON.parse(P.localStorage.getItem(el.dataset.asv2) || 'null'); } catch(e) {}
        if (o && o.v && Date.now() - o.t < MAX_AGE) setNative(el, o.v);
      });
    });
  }
  attach(); setInterval(attach, 1500);
})();
</script>"""

# ===================== 🔑 선생님 조회 화면: 학생마다 입력칸을 따로 만들기 =====================
# Streamlit 은 같은 이름(key)의 입력칸이면 화면(브라우저)에 남아 있던 값을 계속 보여 줌 → 다른 학생을 골라도 앞 학생 이름이 보이는 문제.
# 선생님이 학생 자료를 볼 때만 모든 입력칸 이름 뒤에 '__학생키'를 붙여, 학생마다 새 입력칸으로 그려지게 함.
_WK_FUNCS = ["text_input", "text_area", "selectbox", "radio", "number_input", "slider", "select_slider", "checkbox",
             "multiselect", "date_input", "time_input", "file_uploader", "data_editor", "feedback", "toggle", "pills", "segmented_control"]

def _wk(key):
    try:
        sfx = st.session_state.get("_wkey_suffix")
    except Exception:
        sfx = None
    if key is None or not sfx or str(key).endswith(sfx):
        return key
    return f"{key}{sfx}"

def _install_widget_scoping():
    if getattr(st, "_wk_patched", False):
        return
    def wrap_bound(f):
        def w(*a, **k):
            if k.get("key") is not None:
                k["key"] = _wk(k["key"])
            return f(*a, **k)
        w.__wrapped__ = f
        return w
    def wrap_method(f):
        def w(self, *a, **k):
            if k.get("key") is not None:
                k["key"] = _wk(k["key"])
            return f(self, *a, **k)
        w.__wrapped__ = f
        return w
    try:
        from streamlit.delta_generator import DeltaGenerator as _DG
    except Exception:
        _DG = None
    for name in _WK_FUNCS:
        if _DG is not None and hasattr(_DG, name):
            try: setattr(_DG, name, wrap_method(getattr(_DG, name)))
            except Exception: pass
        if hasattr(st, name):
            try: setattr(st, name, wrap_bound(getattr(st, name)))
            except Exception: pass
    st._wk_patched = True

_install_widget_scoping()

def _rss_mb():
    """현재 앱이 쓰는 메모리(MB). 리눅스 서버에서만 확인 가능"""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except Exception:
        pass
    return None

def memory_release():
    """다 쓰고 버린 메모리를 운영체제에 돌려줌.
    (파이썬은 큰 자료를 처리한 뒤 버린 메모리를 붙잡고 있어서, 구글 시트에서 자료를 불러온 직후 등에 사용량이 크게 보임)"""
    import gc as _gc
    _gc.collect()
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass
    return _rss_mb()

def _memory_guard():
    """1분에 한 번 메모리 확인: 400MB 넘으면 버린 메모리 반납, 그래도 700MB 넘으면 미리보기 그림 등 임시 자료도 비움"""
    import time as _t
    now = _t.time()
    if now - getattr(st, "_mem_checked", 0) < 60:
        return
    st._mem_checked = now
    mb = _rss_mb()
    if mb and mb > 400:
        mb = memory_release()
    if mb and mb > 700:
        try: st.cache_data.clear()
        except Exception: pass
        mb = memory_release()
    st._mem_last = mb

_memory_guard()

def _star_picker(label, key, prev, disabled):
    """별 5개를 직접 눌러 점수를 고르는 칸 (1~5점, 0 = 아직 안 누름)"""
    st.markdown(f"**{label}**")
    if hasattr(st, "feedback"):
        if _wk(key) not in st.session_state and prev > 0:
            st.session_state[_wk(key)] = int(prev) - 1     # 저장된 별 개수로 시작
        try:
            v = st.feedback("stars", key=key, disabled=disabled)
        except TypeError:
            v = st.feedback("stars", key=key)
        n = 0 if v is None else int(v) + 1
        st.caption(f"{'★' * n}{'☆' * (5 - n)}  ({n}점)" if n else "☆ 별을 눌러 점수를 고르세요")
        return n
    return st.select_slider(label, options=[0, 1, 2, 3, 4, 5], value=int(prev), disabled=disabled, label_visibility="collapsed",
                            format_func=lambda v: "미평가" if v == 0 else "★" * v + "☆" * (5 - v), key=key)

def lock_table_headers():
    """활동지 표의 맨 윗줄(제목줄)을 눌러도 정렬·열 이동이 일어나지 않게 투명 덮개를 씌움.
    (휴대폰·태블릿에서 제목줄을 잘못 건드려 행·열 순서가 바뀌던 문제 방지. 칸 입력은 그대로 가능)"""
    st.markdown("""<style>
    [data-testid="stDataFrame"], [data-testid="stDataEditor"], div.stDataFrame { position: relative; }
    [data-testid="stDataFrame"]::after, [data-testid="stDataEditor"]::after, div.stDataFrame::after {
        content: ""; position: absolute; left: 0; right: 0; top: 0; height: 35px;
        z-index: 20; background: transparent; cursor: default; pointer-events: auto;
    }
    </style>""", unsafe_allow_html=True)

# ===================== 🧱 활동지 표 (순서가 절대 바뀌지 않는 칸 입력 표) =====================
GRID_CSS = """<style>
[class*="st-key-tblgrid_"] [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; gap: 4px !important; margin-bottom: -10px; }
[class*="st-key-tblgrid_"] [data-testid="stColumn"], [class*="st-key-tblgrid_"] [data-testid="column"] { min-width: 0 !important; }
[class*="st-key-tblgrid_"] textarea { font-size: 14.5px; line-height: 1.45; }
.tg-head { background:#eef2f7; border:1px solid #cbd5e1; border-radius:6px; padding:8px 10px; font-weight:800; color:#1e293b;
           font-size:14.5px; line-height:1.4; white-space:pre-wrap; word-break:keep-all; overflow-wrap:anywhere; height:100%; }
.tg-head small { display:block; font-weight:500; color:#64748b; font-size:12.5px; margin-top:3px; }
.tg-label { background:#f8fafc; border:1px solid #cbd5e1; border-radius:6px; padding:8px 10px; font-weight:700; color:#0f172a;
            font-size:14.5px; line-height:1.45; white-space:pre-wrap; word-break:keep-all; overflow-wrap:anywhere; min-height:68px; }
.tg-cell { border:1px solid #e2e8f0; border-radius:6px; padding:8px 10px; color:#111; font-size:14.5px; line-height:1.45;
           white-space:pre-wrap; overflow-wrap:anywhere; min-height:44px; background:#fff; }
</style>"""

def _tg_cfg(column_config, col):
    c = (column_config or {}).get(col)
    if isinstance(c, str):
        return {"label": c}
    if isinstance(c, dict):
        tc = c.get("type_config") or {}
        return {"label": c.get("label") or col, "help": c.get("help"), "width": c.get("width"),
                "type": tc.get("type"), "options": tc.get("options")}
    return {"label": col}

def _tg_norm(v):
    import re as _re2
    return _re2.sub(r"\s+", " ", str(v or "")).strip()

def _tg_esc(v):
    import html as _h
    return _h.escape(str(v if v is not None else ""))

def grid_editor(data, *, key, column_config=None, disabled=False, num_rows="fixed", lock_cols=None, **_ignored):
    """st.data_editor 대신 쓰는 활동지 표.
    - 칸마다 입력칸이 따로 있어 제목줄을 눌러도 정렬·열 이동·행 순서 변경이 절대 일어나지 않음
    - 제목(맨 윗줄)과 고정 항목(맨 왼쪽 열)은 글자가 모두 보이게 줄바꿈 표시, 학생이 지우거나 고칠 수 없음
    - 줄 추가가 가능한 표는 [➕ 줄 추가] / [➖ 마지막 줄 삭제] 버튼으로만 줄 수 변경 (고정 항목 줄은 삭제 불가)
    반환값은 st.data_editor 와 같은 DataFrame (열 순서 그대로)."""
    import hashlib
    df = data if isinstance(data, pd.DataFrame) else pd.DataFrame(data)
    cols = [str(c) for c in df.columns]
    df.columns = cols
    base = df.astype(object).where(pd.notna(df), None).to_dict("records")
    cfgs = {c: _tg_cfg(column_config, c) for c in cols}
    for c in cols:
        if not cfgs[c].get("type") and len(df) and df[c].dtype == bool:
            cfgs[c]["type"] = "checkbox"
    # 🔒 고정(잠금) 칸 판단
    locked_all = set()
    lock_vals = {}
    if isinstance(disabled, (list, tuple, set)):
        locked_all |= {str(c) for c in disabled}
    for c, rule in (lock_cols or {}).items():
        if rule == "all":
            locked_all.add(c)
        else:
            lock_vals[c] = {_tg_norm(v) for v in rule}
    if num_rows != "dynamic" and cols and base and all(_tg_norm(r.get(cols[0])) for r in base) \
            and cfgs[cols[0]].get("type") in (None, "text"):
        locked_all.add(cols[0])           # 고정 줄 표의 맨 왼쪽 열이 모두 채워져 있으면 '항목 이름' 열로 보고 잠금
    def is_locked(r, c):
        if c in locked_all:
            return True
        return c in lock_vals and _tg_norm(r.get(c)) in lock_vals[c] and _tg_norm(r.get(c)) != ""
    read_only = disabled is True
    widths = []
    for c in cols:
        w = cfgs[c].get("width")
        widths.append({"small": 1.0, "medium": 2.0, "large": 3.0}.get(w, 1.0 if cfgs[c].get("type") in ("checkbox", "number") else 2.0))
    scope = st.session_state.get("_render_now", "")
    p = "tg_" + hashlib.md5((scope + "|" + key + "|" + json.dumps(base, ensure_ascii=False, default=str, sort_keys=True)).encode("utf-8")).hexdigest()[:12]
    nkey = p + "::n"
    if nkey not in st.session_state:
        st.session_state[nkey] = len(base)
    n = st.session_state[nkey] if (num_rows == "dynamic" and not read_only) else len(base)
    blank = {c: (False if cfgs[c].get("type") == "checkbox" else None if cfgs[c].get("type") == "number" else "") for c in cols}
    rows = [dict(base[i]) if i < len(base) else dict(blank) for i in range(n)]

    st.markdown(GRID_CSS, unsafe_allow_html=True)
    try:
        box = st.container(key=f"tblgrid_{p}")
    except TypeError:
        box = st.container()
    out = []
    with box:
        hc = st.columns(widths)
        for j, c in enumerate(cols):
            hlp = cfgs[c].get("help")
            hc[j].markdown(f"<div class='tg-head'>{_tg_esc(cfgs[c]['label'])}{'<small>' + _tg_esc(hlp) + '</small>' if hlp else ''}</div>", unsafe_allow_html=True)
        for i, r in enumerate(rows):
            # 줄 높이: 이 줄에서 가장 긴 글이 다 보이도록
            lines = 2
            for j, c in enumerate(cols):
                v = str(st.session_state.get(f"{p}::{i}::{j}", r.get(c)) or "")
                per = max(8, int(widths[j] * 11))
                lines = max(lines, sum(max(1, -(-len(seg) // per)) for seg in v.split("\n")))
            h = int(max(68, min(260, 18 + lines * 22)))
            rc = st.columns(widths)
            new = {}
            row_label = _tg_norm(r.get(cols[0])) or f"{i + 1}번째 줄"
            for j, c in enumerate(cols):
                k = f"{p}::{i}::{j}"
                v = r.get(c)
                t = cfgs[c].get("type")
                with rc[j]:
                    if read_only or is_locked(r, c):
                        if t == "checkbox":
                            st.markdown(f"<div class='tg-cell' style='text-align:center'>{'✔' if v else ''}</div>", unsafe_allow_html=True)
                        else:
                            cls = "tg-label" if is_locked(r, c) else "tg-cell"
                            st.markdown(f"<div class='{cls}'>{_tg_esc('' if v is None else v)}</div>", unsafe_allow_html=True)
                        new[c] = v
                    elif t == "checkbox":
                        new[c] = st.checkbox(f"{cfgs[c]['label']} ({row_label})", key=k, label_visibility="collapsed", **_sv(k, bool(v)))
                    elif t == "selectbox":
                        opts = list(cfgs[c].get("options") or [])
                        if v not in opts and v not in (None, ""):
                            opts = [v] + opts
                        if not opts:
                            opts = [""]
                        idx = opts.index(v) if v in opts else 0
                        new[c] = st.selectbox(f"{cfgs[c]['label']} ({row_label})", opts, key=k, label_visibility="collapsed",
                                              **({} if k in st.session_state else {"index": idx}))
                    elif t == "number":
                        try:
                            nv = None if v in (None, "") else int(float(v))
                        except Exception:
                            nv = None
                        new[c] = st.number_input(f"{cfgs[c]['label']} ({row_label})", step=1, key=k, label_visibility="collapsed", **_sv(k, nv))
                    else:
                        new[c] = st.text_area(f"{cfgs[c]['label']} ({row_label})", key=k, height=h, label_visibility="collapsed",
                                              placeholder=str(cfgs[c]["label"]), **_sv(k, "" if v is None else str(v)))
            out.append(new)
        if num_rows == "dynamic" and not read_only:
            min_n = max([i + 1 for i, r in enumerate(rows) if any(is_locked(r, c) for c in cols)] + [1])
            def _add():
                st.session_state[nkey] = st.session_state.get(nkey, n) + 1
            def _del():
                cur = st.session_state.get(nkey, n)
                if cur > min_n:
                    for jj in range(len(cols)):
                        st.session_state.pop(f"{p}::{cur - 1}::{jj}", None)
                    st.session_state[nkey] = cur - 1
            b1, b2, _sp = st.columns([1, 1, 3])
            b1.button("➕ 줄 추가", key=p + "::add", on_click=_add, use_container_width=True)
            b2.button("➖ 마지막 줄 삭제", key=p + "::del", on_click=_del, disabled=n <= min_n, use_container_width=True)
    res = pd.DataFrame(out, columns=cols) if out else pd.DataFrame(columns=cols)
    return res


def inject_custom_scripts(scope_key, clear=False):
    """브라우저 임시저장: 학생 + 화면별로 분리 저장, 같은 이름 문항도 칸별로 분리, 저장 성공 시 비움"""
    import hashlib
    scope = hashlib.md5(scope_key.encode("utf-8")).hexdigest()[:12]
    components.html(AUTOSAVE_JS.replace("__SCOPE__", scope).replace("__CLEAR__", "true" if clear else "false"), height=0, width=0)

# --- [4] 활동지 화면 구성 함수 ---
def render_group_members(ans, disabled_flag, category=""):
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 20px; margin-bottom: 15px;'>👥 모둠 구성원 (학번/이름)</h3>", unsafe_allow_html=True)
    col_m1, col_m2, col_m3, col_m4 = st.columns(4)
    default_id = st.session_state.user_info.get("id", "") if st.session_state.user_info.get("role") == "학생" else ""
    default_name = st.session_state.user_info.get("name", "") if st.session_state.user_info.get("role") == "학생" else ""
    m1_id = col_m1.text_input("모둠원1(모둠장) 학번", value=ans.get("m1_id", default_id), disabled=disabled_flag, key=f"m1_id_{category}")
    m1_name = col_m1.text_input("모둠원1 이름", value=ans.get("m1_name", default_name), disabled=disabled_flag, key=f"m1_name_{category}")
    m2_id = col_m2.text_input("모둠원2 학번", value=ans.get("m2_id", ""), disabled=disabled_flag, key=f"m2_id_{category}")
    m2_name = col_m2.text_input("모둠원2 이름", value=ans.get("m2_name", ""), disabled=disabled_flag, key=f"m2_name_{category}")
    m3_id = col_m3.text_input("모둠원3 학번", value=ans.get("m3_id", ""), disabled=disabled_flag, key=f"m3_id_{category}")
    m3_name = col_m3.text_input("모둠원3 이름", value=ans.get("m3_name", ""), disabled=disabled_flag, key=f"m3_name_{category}")
    m4_id = col_m4.text_input("모둠원4 학번", value=ans.get("m4_id", ""), disabled=disabled_flag, key=f"m4_id_{category}")
    m4_name = col_m4.text_input("모둠원4 이름", value=ans.get("m4_name", ""), disabled=disabled_flag, key=f"m4_name_{category}")
    st.markdown("---")
    return m1_id, m1_name, m2_id, m2_name, m3_id, m3_name, m4_id, m4_name

def render_activity1_3th(user_key, u_info, current_role):
    category = ACT_3_1
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    ans = apply_draft(user_key, category, ans, enabled=(owner_key == user_key))
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    if current_role == "관리자": disabled_flag = False; st.info("💡 교사/관리자 모드입니다.")
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    if current_role == "학생":
        if disabled_flag: st.error(status_msg, icon="🚫")
        else: st.success(status_msg, icon="✅")
    
    a1_1 = st.text_input("1. 영상의 제목", value=ans.get("a1_1", ""), disabled=disabled_flag, key=f"a1_1_{category}")
    a1_2 = st.text_input("2. 영상에 등장하는 국가/지역", value=ans.get("a1_2", ""), disabled=disabled_flag, key=f"a1_2_{category}")
    a1_3 = st.text_area("3. 영상을 선택하게 된 이유", value=ans.get("a1_3", ""), disabled=disabled_flag, key=f"a1_3_{category}")
    st.markdown("---")
    a2_1 = st.text_area("1. 첫 느낌", value=ans.get("a2_1", ""), disabled=disabled_flag, key=f"a2_1_{category}")
    a2_2_1 = st.text_input("▶ 인상적이었던 장소/공간:", value=ans.get("a2_2_1", ""), disabled=disabled_flag, key=f"a2_2_1_{category}")
    a2_2_2 = st.text_area("▶ 이유:", value=ans.get("a2_2_2", ""), disabled=disabled_flag, key=f"a2_2_2_{category}")
    a2_3_1 = st.text_input("▶ 누구에게 추천:", value=ans.get("a2_3_1", ""), disabled=disabled_flag, key=f"a2_3_1_{category}")
    a2_3_2 = st.text_area("▶ 추천하는 이유:", value=ans.get("a2_3_2", ""), disabled=disabled_flag, key=f"a2_3_2_{category}")
    a2_4 = st.text_area("4. 나만의 감상평", value=ans.get("a2_4", ""), disabled=disabled_flag, key=f"a2_4_{category}")
    st.markdown("---")
    a3_1 = st.text_input("1) 영상의 제목:", value=ans.get("a3_1", ""), disabled=disabled_flag, key=f"a3_1_{category}")
    a3_2 = st.text_input("2) 주요 컨셉/느낌:", value=ans.get("a3_2", ""), disabled=disabled_flag, key=f"a3_2_{category}")
    a3_3 = st.text_input("3) 누구와 함께 가고 싶은가?:", value=ans.get("a3_3", ""), disabled=disabled_flag, key=f"a3_3_{category}")
    a3_4 = st.text_area("4) 그 이유는?:", value=ans.get("a3_4", ""), disabled=disabled_flag, key=f"a3_4_{category}")
    a3_5 = st.text_input("5) 가장 해 보고 싶은 것:", value=ans.get("a3_5", ""), disabled=disabled_flag, key=f"a3_5_{category}")
    a3_6 = st.text_area("6) 그 이유는?:", value=ans.get("a3_6", ""), key=f"a3_6_{category}", disabled=disabled_flag)
    a3_7 = st.text_input("7) 꼭 넣고 싶은 장소/공간:", value=ans.get("a3_7", ""), disabled=disabled_flag, key=f"a3_7_{category}")
    a3_8 = st.text_area("8) 그 이유는?:", value=ans.get("a3_8", ""), key=f"a3_8_{category}", disabled=disabled_flag)
    a3_9 = st.text_area("9) 썸네일 영상 기획:", value=ans.get("a3_9", ""), disabled=disabled_flag, key=f"a3_9_{category}")
    a3_10 = st.text_input("10) 어울리는 BGM:", value=ans.get("a3_10", ""), disabled=disabled_flag, key=f"a3_10_{category}")
    a3_11 = st.text_area("11) 그 이유는?:", value=ans.get("a3_11", ""), key=f"a3_11_{category}", disabled=disabled_flag)
    
    new_ans = {"a1_1": a1_1, "a1_2": a1_2, "a1_3": a1_3, "a2_1": a2_1, "a2_2_1": a2_2_1, "a2_2_2": a2_2_2, "a2_3_1": a2_3_1, "a2_3_2": a2_3_2, "a2_4": a2_4, "a3_1": a3_1, "a3_2": a3_2, "a3_3": a3_3, "a3_4": a3_4, "a3_5": a3_5, "a3_6": a3_6, "a3_7": a3_7, "a3_8": a3_8, "a3_9": a3_9, "a3_10": a3_10, "a3_11": a3_11}
    autosave_draft(user_key, category, new_ans, disabled_flag)  # 🛟 서버 임시저장
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        commit_activity(user_key, category, new_ans, u_name)

def render_activity2_3th(user_key, u_info, current_role):
    category = ACT_3_2
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    ans = apply_draft(user_key, category, ans, enabled=(owner_key == user_key))
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    if current_role == "관리자": disabled_flag = False; st.info("💡 교사/관리자 모드입니다.")
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    if current_role == "학생":
        if disabled_flag: st.error(status_msg, icon="🚫")
        else: st.success(status_msg, icon="✅")
        
    q1_1 = st.text_input("1-1) 나에게 편안함을 주는 장소가 있는가?", value=ans.get("q1_1", ""), disabled=disabled_flag, key=f"q1_1_{category}")
    q1_2 = st.text_area("1-2) 어떤 면에서 편안함을 주는가?", value=ans.get("q1_2", ""), disabled=disabled_flag, key=f"q1_2_{category}")
    q2_1 = st.text_input("2-1) 자신의 성격은?", value=ans.get("q2_1", ""), disabled=disabled_flag, key=f"q2_1_{category}")
    q2_2 = st.text_input("2-2) 성격 형성에 영향을 준 장소가 있는가?", value=ans.get("q2_2", ""), disabled=disabled_flag, key=f"q2_2_{category}")
    q2_3 = st.text_area("2-3) 그 이유는 무엇 때문인가?", value=ans.get("q2_3", ""), disabled=disabled_flag, key=f"q2_3_{category}")
    q3_1 = st.text_input("3-1) 자신의 장점은?", value=ans.get("q3_1", ""), disabled=disabled_flag, key=f"q3_1_{category}")
    q3_2 = st.text_input("3-2) 장점 형성에 영향을 준 장소가 있는가?", value=ans.get("q3_2", ""), disabled=disabled_flag, key=f"q3_2_{category}")
    q3_3 = st.text_area("3-3) 그 이유는 무엇 때문인가?", value=ans.get("q3_3", ""), disabled=disabled_flag, key=f"q3_3_{category}") 
    q4_1 = st.text_input("4-1) 성장함에 있어 영향을 준 장소가 있는가?", value=ans.get("q4_1", ""), disabled=disabled_flag, key=f"q4_1_{category}")
    q4_2 = st.text_area("4-2) 어떤 면에서 영향을 주었는가?", value=ans.get("q4_2", ""), disabled=disabled_flag, key=f"q4_2_{category}")
    q5_1 = st.text_input("5-1) 지금 나의 목표는?", value=ans.get("q5_1", ""), disabled=disabled_flag, key=f"q5_1_{category}")
    q5_2 = st.text_area("5-2) 목표 설정에 영향을 준 장소가 있는가?", value=ans.get("q5_2", ""), disabled=disabled_flag, key=f"q5_2_{category}")
    q6_1 = st.text_input("6-1) 소중한 사람에게 소개해 주고 싶은 장소가 있는가?", value=ans.get("q6_1", ""), disabled=disabled_flag, key=f"q6_1_{category}")
    q6_2 = st.text_area("6-2) 그 이유는?", value=ans.get("q6_2", ""), disabled=disabled_flag, key=f"q6_2_{category}")
    q7_1 = st.text_input("7-1) 나만의 비밀 장소가 있는가?", value=ans.get("q7_1", ""), disabled=disabled_flag, key=f"q7_1_{category}")
    q7_2 = st.text_area("7-2) 그 이유는?", value=ans.get("q7_2", ""), disabled=disabled_flag, key=f"q7_2_{category}")
    q8_1 = st.text_input("8-1) 과거로 돌아갈 수 있다면 다시 가고 싶은 장소는?", value=ans.get("q8_1", ""), disabled=disabled_flag, key=f"q8_1_{category}")
    q8_2 = st.text_area("8-2) 그 이유는?", value=ans.get("q8_2", ""), disabled=disabled_flag, key=f"q8_2_{category}")
    
    new_ans = {"q1_1": q1_1, "q1_2": q1_2, "q2_1": q2_1, "q2_2": q2_2, "q2_3": q2_3, "q3_1": q3_1, "q3_2": q3_2, "q3_3": q3_3, "q4_1": q4_1, "q4_2": q4_2, "q5_1": q5_1, "q5_2": q5_2, "q6_1": q6_1, "q6_2": q6_2, "q7_1": q7_1, "q7_2": q7_2, "q8_1": q8_1, "q8_2": q8_2}
    autosave_draft(user_key, category, new_ans, disabled_flag)  # 🛟 서버 임시저장
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        commit_activity(user_key, category, new_ans, u_name)

def render_activity3_3th(user_key, u_info, current_role):
    category = ACT_3_3
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    ans = apply_draft(user_key, category, ans, enabled=(owner_key == user_key))
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    if current_role == "관리자": disabled_flag = False; st.info("💡 교사/관리자 모드입니다.")
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    if current_role == "학생":
        if disabled_flag: st.error(status_msg, icon="🚫")
        else: st.success(status_msg, icon="✅")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>1. 세계 인식 수준에 대한 확인</h3>", unsafe_allow_html=True)
    levels = ["선택", "매우높음", "높음", "보통", "낮음", "매우낮음"]
    s1_dis = True if disabled_flag else ["대륙"]
    s1_df = pd.DataFrame(ans.get("s1_df", [{"대륙": c, "관심도": "선택", "지식수준": "선택"} for c in ["아시아", "유럽", "북아메리카", "남아메리카", "아프리카", "오세아니아"]]))
    
    st.markdown("**1. 대륙별 관심도 및 지식 수준 체크**")
    edited_s1_df = grid_editor(s1_df, column_config={"관심도": st.column_config.SelectboxColumn("관심도", options=levels), "지식수준": st.column_config.SelectboxColumn("지식수준", options=levels)}, disabled=s1_dis, hide_index=True, use_container_width=True, key=f"s1_df_{category}")
    
    st.markdown("**2. (개인적 경험에 기반) 특정 국가에 대한 기억과 인상에 대한 분석**")
    st.caption("[직접 경험]")
    direct_df = pd.DataFrame(ans.get("direct_df", [{"여행해 본 국가": "", "해당 국가에 대한 구체적인 기억 혹은 인상": ""} for _ in range(3)]))
    edited_direct_df = grid_editor(direct_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"direct_df_{category}")
    
    st.caption("[간접 경험]")
    ind1 = st.text_input("즐겨 보는 외국 영화/드라마는 어느 나라 작품?", value=ans.get("ind1", ""), disabled=disabled_flag, key=f"ind1_{category}")
    ind2 = st.text_input("좋아하는 음악가나 연예인이 있다면 어느 나라?", value=ans.get("ind2", ""), disabled=disabled_flag, key=f"ind2_{category}")
    ind3 = st.text_input("자주 먹는 외국 음식이 있다면 어느 나라?", value=ans.get("ind3", ""), disabled=disabled_flag, key=f"ind3_{category}")
    
    st.markdown("**3. 꼭 가보고 싶은 Top 5 국가와 그 이유**")
    top5_want = pd.DataFrame(ans.get("top5_want", [{"국가 혹은 지역": "", "이유": ""} for _ in range(5)]))
    edited_top5_want = grid_editor(top5_want, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"top5_want_{category}")
    
    st.markdown("**4. 절대 가고 싶지 않은 Top 5 국가와 그 이유**")
    top5_notwant = pd.DataFrame(ans.get("top5_notwant", [{"국가 혹은 지역": "", "이유": ""} for _ in range(5)]))
    edited_top5_notwant = grid_editor(top5_notwant, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"top5_notwant_{category}")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>2. 특정 대륙/국가에 대한 자신의 편견과 고정관념</h3>", unsafe_allow_html=True)
    st.markdown("**1. 국가별 한 단어 라벨링**")
    label_df = pd.DataFrame(ans.get("label_df", [{"가 보고 싶은 국가": "", "한 단어 라벨": "", "가고 싶지 않은 국가": "", "한 단어 라벨(부정)": ""} for _ in range(3)]))
    edited_label_df = grid_editor(label_df, column_config={"한 단어 라벨(부정)": st.column_config.TextColumn("한 단어 라벨")}, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"label_df_{category}")
    
    st.markdown("**2. 개인적으로 가장 강한 편견을 가진 국가**")
    prej_df = pd.DataFrame(ans.get("prej_df", [{"국가명": "", "편견 내용": "", "편견 형성 과정 혹은 이유": ""} for _ in range(2)]))
    edited_prej_df = grid_editor(prej_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"prej_df_{category}")
    
    st.markdown("**3. 미디어와 교육의 영향으로 인한 인식 발견**")
    col1, col2 = st.columns(2)
    media1_1 = col1.text_area("뉴스에서 자주 접하는 국가들", value=ans.get("media1_1", ""), height=80, disabled=disabled_flag, key=f"media1_1_{category}")
    media1_2 = col2.text_area("그 나라들에 대한 이미지", value=ans.get("media1_2", ""), height=80, disabled=disabled_flag, key=f"media1_2_{category}")
    media2_1 = col1.text_area("영화/드라마에서 자주 접하는 국가들", value=ans.get("media2_1", ""), height=80, disabled=disabled_flag, key=f"media2_1_{category}")
    media2_2 = col2.text_area("그 나라들에 대한 이미지", value=ans.get("media2_2", ""), height=80, disabled=disabled_flag, key=f"media2_2_{category}")
    media3_1 = col1.text_area("학교에서 많이 배운 국가들", value=ans.get("media3_1", ""), height=80, disabled=disabled_flag, key=f"media3_1_{category}")
    media3_2 = col2.text_area("그 나라들에 대한 지식", value=ans.get("media3_2", ""), height=80, disabled=disabled_flag, key=f"media3_2_{category}")
    
    st.markdown("**4. 부정확한 정보나 과장된 인식 발견** (▶ 사실과 다른 내용들)")
    fake_df = pd.DataFrame(ans.get("fake_df", [{"국가명": "", "잘못 알고 있었던 내용": "", "실제 사실": ""} for _ in range(3)]))
    edited_fake_df = grid_editor(fake_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"fake_df_{category}")
    
    st.markdown("**5. 우월감이나 차별 의식 점검**")
    discrim_df = pd.DataFrame(ans.get("discrim_df", [{"어떤 국가에 대해?": "", "어떤 측면에서": "", "그 이유": ""} for _ in range(2)]))
    edited_discrim_df = grid_editor(discrim_df, column_config={"그 이유": st.column_config.TextColumn("우월감이나 차별 의식을 느끼는 부분")}, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"discrim_df_{category}")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>3. 포용적이고 균형잡힌 세계관을 위한 노력</h3>", unsafe_allow_html=True)
    st.markdown("**1. 편견을 바꾸고 싶은 국가**")
    change_df = pd.DataFrame(ans.get("change_df", [{"어떤 국가에 대해?": "", "현재의 편견": "", "올바른 정보를 찾기 위한 계획": ""} for _ in range(2)]))
    edited_change_df = grid_editor(change_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"change_df_{category}")
    
    st.markdown("**2. 가장 무관심했던 대륙 혹은 국가**")
    ignore_df = pd.DataFrame(ans.get("ignore_df", [{"선택 대륙/국가": "", "무관심 이유": "", "관심 확장을 위한 정보 수집 방법": ""} for _ in range(2)]))
    edited_ignore_df = grid_editor(ignore_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"ignore_df_{category}")
    
    st.markdown("**3. 서구 중심적 시각에서 벗어나기**")
    western_df = pd.DataFrame(ans.get("western_df", [{"현재 가지고 있는 서구 중심적 시각": "", "개선 방법": ""} for _ in range(2)]))
    edited_western_df = grid_editor(western_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"western_df_{category}")

    st.markdown("**4. 약소국 관점 이해하기**")
    weak_df = pd.DataFrame(ans.get("weak_df", [{"주목해 볼 국가": "", "그 이유": ""} for _ in range(2)]))
    edited_weak_df = grid_editor(weak_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"weak_df_{category}")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>4. 목표로 하는 세계관</h3>", unsafe_allow_html=True)
    goal_1 = st.text_area("▶ 어떤 사람이 되고 싶은가?", value=ans.get("goal_1", ""), height=100, disabled=disabled_flag, key=f"goal_1_{category}")
    goal_2 = st.text_area("▶ 어떤 세계관을 갖고 싶은가?", value=ans.get("goal_2", ""), height=100, disabled=disabled_flag, key=f"goal_2_{category}")
    
    new_ans = {
        "s1_df": edited_s1_df.to_dict('records'), "direct_df": edited_direct_df.to_dict('records'), 
        "ind1": ind1, "ind2": ind2, "ind3": ind3, "top5_want": edited_top5_want.to_dict('records'), 
        "top5_notwant": edited_top5_notwant.to_dict('records'), "label_df": edited_label_df.to_dict('records'), 
        "prej_df": edited_prej_df.to_dict('records'), "media1_1": media1_1, "media1_2": media1_2, 
        "media2_1": media2_1, "media2_2": media2_2, "media3_1": media3_1, "media3_2": media3_2, 
        "fake_df": edited_fake_df.to_dict('records'), "discrim_df": edited_discrim_df.to_dict('records'), 
        "change_df": edited_change_df.to_dict('records'), "ignore_df": edited_ignore_df.to_dict('records'), 
        "western_df": edited_western_df.to_dict('records'), "weak_df": edited_weak_df.to_dict('records'),
        "goal_1": goal_1, "goal_2": goal_2
    }
    autosave_draft(user_key, category, new_ans, disabled_flag)  # 🛟 서버 임시저장
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        commit_activity(user_key, category, new_ans, u_name)

def render_activity1_2nd(user_key, u_info, current_role):
    category = ACT_2_1
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    ans = apply_draft(user_key, category, ans, enabled=(owner_key == user_key))
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    is_member_view = False
    if current_role == "학생" and owner_key != user_key:
        is_member_view = True; disabled_flag = True
    if current_role == "관리자": disabled_flag = False; st.info("💡 교사/관리자 모드입니다.")
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000;'>♣ {category}</h2>", unsafe_allow_html=True)
    if is_member_view: st.info("💡 **[조회 전용]** 모둠장(대표)이 작성 및 저장한 화면을 연동하여 조회 중입니다.")
    elif current_role == "학생":
        if disabled_flag: st.error(status_msg.replace('\n', '<br>'), icon="🚫")
        else: st.success(status_msg, icon="✅")

    m1_id, m1_name, m2_id, m2_name, m3_id, m3_name, m4_id, m4_name = render_group_members(ans, disabled_flag, category)
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 1. 우리 지역에 대한 '밈' 수집 및 지리 정보 팩트 체크 일지</h3>", unsafe_allow_html=True)
    st.markdown("<div class='guide-box'><strong>▶ 교과서 13쪽 내용 中</strong><br>개인이 여러 장소에서 경험을 쌓으며 형성하는 주관적인 감정을 장소감이라 합니다. 이 장소감이 여러 사람에게 공유되면서 형성된 독특한 이미지가 바로 장소성이며, 이것이 확장되어 그 도시만의 독특한 특성인 도시 정체성을 만듭니다.<br><br><strong>* 나만의 주관적 장소감 성찰</strong><br>타 지역 사람들의 선입견과 달리, '우리 지역에서 나를 성장시킨 장소'나 '우리가 가장 애착을 느끼는 장소'를 적고 그에 대한 우리의 감정이나 생각을 적어 보세요.</div>", unsafe_allow_html=True)
    step1_1 = st.text_area("1. 우리가 선택한 우리 지역의 밈", value=ans.get("step1_1", ""), height=70, disabled=disabled_flag)
    step1_2 = st.text_area("2. 이 밈이 대중에게 심어준 주관적 이미지 (편견 혹은 선입견)", value=ans.get("step1_2", ""), height=80, disabled=disabled_flag)
    step1_new1 = st.text_area("3. 왜 그런 '밈'이 생기게 되었을까? (주관적 생각)", value=ans.get("step1_new1", ""), height=100, disabled=disabled_flag)
    step1_new2 = st.text_area("4. 해당 '밈'이 생기게 된 이유를 지리적 관점에서 생각해 본다면?", value=ans.get("step1_new2", ""), height=100, disabled=disabled_flag)
    step1_3 = st.text_area("5. 우리 모둠에게 특별한 장소감을 주는 장소", value=ans.get("step1_3", ""), height=70, disabled=disabled_flag)
    step1_4 = st.text_area("6. 그 장소에서 느끼는 감정이나 생각", value=ans.get("step1_4", ""), height=100, disabled=disabled_flag)
    
    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 2. 도시 발달 과정과 객관적 지표</h3>", unsafe_allow_html=True)
    st.markdown("<div class='guide-box'><strong>▶ 교과서 14~15, 31~32쪽 내용 中</strong><br>객관적 의미의 도시는 시가지로 구성되며 2·3차 산업 비율이 높은 공간입니다. 도시는 살아있는 생명체처럼 탄생, 성장, 정체, 쇠퇴, 전환의 도시 발달 과정을 겪습니다. 울산은 시대별로 역동적인 변화를 거쳐왔습니다.<br><br><strong>* 울산의 역사적 발달 과정 추적</strong><br>다음 제시된 울산의 발달 역사 중 우리 조가 탐구할 시기를 선택하고, 당시 울산의 핵심 공간과 객관적 특징을 매칭해 보세요.<br><br><strong>* 지리 데이터 기반 분석</strong><br>우리 모둠이 선택한 시기 울산의 객관적 지표를 지리 정보 서비스나 통계 자료를 통해 확인해 보세요.<br>- 추천 검색어: '울산광역시 통계포털', 'KOSIS 지역별 고용조사', '카카오맵/네이버맵 지적편집도'<br>- 조사한 구체적 사실/통계: 예) 현재 울산의 제조업 종사자 비율이 약 40% 이상으로 전국 최고 수준이라는 점 / 태화강 수질이 생태 등급으로 회복된 지표 등</div>", unsafe_allow_html=True)
    step2_1_period = st.radio("1. 탐구할 시기", ["조선시대", "1960~70년대", "1980~90년대", "2000년대~현재"], index=["조선시대", "1960~70년대", "1980~90년대", "2000년대~현재"].index(ans.get("step2_1_period", "조선시대")) if ans.get("step2_1_period") in ["조선시대", "1960~70년대", "1980~90년대", "2000년대~현재"] else 0, disabled=disabled_flag, horizontal=True, key=f"step2_1_period_{category}")
    step2_1_space = st.text_input("2-1. 핵심 공간", value=ans.get("step2_1_space", ""), disabled=disabled_flag, key=f"step2_1_space_{category}")
    step2_1_feat = st.text_input("2-2. 객관적 특징", value=ans.get("step2_1_feat", ""), disabled=disabled_flag, key=f"step2_1_feat_{category}")
    step2_3 = st.text_area("3. 객관적 지리 데이터 혹은 지표", value=ans.get("step2_3", ""), disabled=disabled_flag, key=f"step2_3_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 3. 살기 좋은 울산의 조건: 거주 적합성 진단</h3>", unsafe_allow_html=True)
    st.markdown("<div class='guide-box'><strong>▶ 교과서 38쪽 내용 中</strong><br>일정한 곳에 머물러 살기 알맞은 조건이나 성질을 거주 적합성이라고 합니다. 이는 지속가능성, 이동성, 안전 및 보안, 서비스 효율성, 경제 성장, 도시 평판 등 삶의 질과 관련된 6대 요소로 이루어집니다. 개인의 연령, 직업, 가치관에 따라 선호하는 거주 적합성은 각기 다르게 나타납니다.<br><br><strong>* 우리의 시선으로 본 울산의 거주 적합성 스코어보드</strong><br>울산에서 살아가는 10대 고등학생인 여러분의 관점에서, 현재 울산의 거주 적합성 요소를 5점 만점으로 평가하고 그 까닭을 서술해 보세요.</div>", unsafe_allow_html=True)
    step3_df = pd.DataFrame(ans.get("step3_df", [{"거주 적합성 요인": "경제 성장", "만족도 점수": "⭐⭐⭐⭐", "한 줄 평가": ""}] + [{"거주 적합성 요인": "", "만족도 점수": "⭐⭐⭐", "한 줄 평가": ""} for _ in range(4)]))
    edited_step3_df = grid_editor(step3_df, column_config={"만족도 점수": st.column_config.SelectboxColumn("만족도 점수", options=["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"])}, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step3_df_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 4. 우리의 방식으로 해 보는 울산 브랜딩: 정체성 리뉴얼</h3>", unsafe_allow_html=True)
    st.markdown("<div class='guide-box'><strong>밈과 지리적 사실의 융합을 통한 '울산성(Ulsan-ity)' 재정의</strong><br>STEP 1~3의 탐구 결과를 바탕으로, 울산의 프레임을 위트 있게 깨부수는 우리 모둠만의 울산 브랜딩 슬로건과 간단한 정책(시설)을 제안해 봅시다.</div>", unsafe_allow_html=True)
    step4_1 = st.text_input("1. 기존 프레임(대중의 오해)", value=ans.get("step4_1", ""), disabled=disabled_flag, key=f"step4_1_{category}")
    step4_2 = st.text_input("2. 기존 프레임에 대한 우리 모둠의 생각", value=ans.get("step4_2", ""), disabled=disabled_flag, key=f"step4_2_{category}")
    step4_3 = st.text_input("3. 기존 프레임에 대한 우리 모둠의 생각을 담은 강력한 슬로건", value=ans.get("step4_3", ""), disabled=disabled_flag, key=f"step4_3_{category}")
    step4_4 = st.text_area("4. 기존 프레임의 부정적인 부분을 상쇄할 수 있는 우리 모둠이 제안하는 울산의 거주 적합성 개선 아이디어", value=ans.get("step4_4", ""), disabled=disabled_flag, key=f"step4_4_{category}")

    new_ans = {
        "m1_id": m1_id, "m1_name": m1_name, "m2_id": m2_id, "m2_name": m2_name, "m3_id": m3_id, "m3_name": m3_name, "m4_id": m4_id, "m4_name": m4_name, 
        "step1_1": step1_1, 
        "step1_2": step1_2, 
        "step1_new1": step1_new1, 
        "step1_new2": step1_new2, 
        "step1_3": step1_3, 
        "step1_4": step1_4, 
        "step2_1_period": step2_1_period, "step2_1_space": step2_1_space, "step2_1_feat": step2_1_feat, "step2_3": step2_3, 
        "step3_df": edited_step3_df.to_dict('records'), 
        "step4_1": step4_1, "step4_2": step4_2, "step4_3": step4_3, "step4_4": step4_4
    }
    autosave_draft(user_key, category, new_ans, disabled_flag)  # 🛟 서버 임시저장
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        commit_activity(user_key, category, new_ans, u_name)

def render_activity2_2nd(user_key, u_info, current_role):
    category = ACT_2_2
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    ans = apply_draft(user_key, category, ans, enabled=(owner_key == user_key))
    
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    group_member_view = current_role == "학생" and owner_key != user_key and _group_lock_on(category)
    if group_member_view:
        disabled_flag = True     # 🔒 모둠원 조회 전용: 모둠 대표 기록의 작성자만 수정·저장
    if current_role == "관리자":
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. (수정 가능)")

    if group_member_view:
        st.info("💡 **[조회 전용]** 모둠 대표가 작성·저장한 화면을 함께 보고 있습니다. 수정과 저장은 모둠 대표만 할 수 있습니다.")
    st.markdown(f"<h2 style='font-size: 26px; font-weight: 900; color: #111; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ [2학년] 수행평가 2 - 내가 설계하는 N분 도시 with 파리의 15분 도시설계</h2>", unsafe_allow_html=True)
    
    if current_role == "학생":
        if disabled_flag:
            st.error(status_msg, icon="🔒")
        else:
            st.success(status_msg, icon="🔓")

    # 🌟 학생별 고유 식별자 (데이터 섞임 원천 차단 핵심 코드)
    k_sfx = user_key if 'user_key' in locals() else current_user_key

    # ----------------------------------------------------
    # 모둠 구성원 (학번/이름)
    # ----------------------------------------------------
    st.markdown("<h4 style='font-size: 18px; font-weight: 700; color: #333;'>👥 모둠 구성원 (학번/이름)</h4>", unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        m1_id = st.text_input("모둠원1(모둠장) 학번", value=ans.get("m1_id", ""), disabled=disabled_flag, key=f"m1_id_{k_sfx}")
        m1_name = st.text_input("모둠원1 이름", value=ans.get("m1_name", ""), disabled=disabled_flag, key=f"m1_name_{k_sfx}")
    with c2:
        m2_id = st.text_input("모둠원2 학번", value=ans.get("m2_id", ""), disabled=disabled_flag, key=f"m2_id_{k_sfx}")
        m2_name = st.text_input("모둠원2 이름", value=ans.get("m2_name", ""), disabled=disabled_flag, key=f"m2_name_{k_sfx}")
    with c3:
        m3_id = st.text_input("모둠원3 학번", value=ans.get("m3_id", ""), disabled=disabled_flag, key=f"m3_id_{k_sfx}")
        m3_name = st.text_input("모둠원3 이름", value=ans.get("m3_name", ""), disabled=disabled_flag, key=f"m3_name_{k_sfx}")
    with c4:
        m4_id = st.text_input("모둠원4 학번", value=ans.get("m4_id", ""), disabled=disabled_flag, key=f"m4_id_{k_sfx}")
        m4_name = st.text_input("모둠원4 이름", value=ans.get("m4_name", ""), disabled=disabled_flag, key=f"m4_name_{k_sfx}")

    st.markdown("<hr style='margin: 25px 0;'>", unsafe_allow_html=True)

    # ----------------------------------------------------
    # Step 1. 우리 동네 현황 진단
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 1. 우리 동네 현황 진단</h3>", unsafe_allow_html=True)

    n_options = ["10분", "15분", "20분", "25분", "30분"]
    saved_n = ans.get("step1_n_min", "15분")
    n_idx = n_options.index(saved_n) if saved_n in n_options else 1

    col_n1, col_n2 = st.columns([1.5, 2.5])
    with col_n1:
        step1_n_min = st.selectbox(
            "⏱️ 우리 모둠 설계 기준 시간 (N분)",
            options=n_options,
            index=n_idx,
            disabled=disabled_flag,
            key=f"s1_n_min_select_{k_sfx}"
        )

    st.markdown(f"""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 20px;'>
        <b style='color: #2b6cb0;'>▶ 도보 {step1_n_min} 생활권 분석</b><br>
        <span style='font-size: 14px; color: #4a5568;'>: 실제 답사와 지도 앱 내용을 통한 필수 서비스 결손 현황 체크</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("**1. 대상 지역** (예: 학교 주변 인근 OO아파트 OO단지 일대)")
    step1_1 = st.text_input("대상 지역 입력", value=ans.get("step1_1", ""), label_visibility="collapsed", disabled=disabled_flag, key=f"s1_1_2_2_{k_sfx}")

    st.markdown(f"<br>**2. {step1_n_min} 생활권 반경 내 필수 서비스 체크리스트**", unsafe_allow_html=True)
    default_s1_df = [
        {"구분": "주거 및 생활", "필수 서비스 항목": "생필품 마트, 일상 편의시설", "충분": False, "부족 or 없음": False},
        {"구분": "의료 및 돌봄", "필수 서비스 항목": "병원, 약국, 돌봄센터", "충분": False, "부족 or 없음": False},
        {"구분": "여가 및 녹지", "필수 서비스 항목": "공원, 수변 공간, 휴식 공간", "충분": False, "부족 or 없음": False},
        {"구분": "교육 및 문화", "필수 서비스 항목": "도서관, 학습 공간, 문화 시설", "충분": False, "부족 or 없음": False},
        {"구분": "이동 및 보행", "필수 서비스 항목": "보행자 전용 도로, 자전거 도로", "충분": False, "부족 or 없음": False},
        {"구분": "", "필수 서비스 항목": "", "충분": False, "부족 or 없음": False},
        {"구분": "", "필수 서비스 항목": "", "충분": False, "부족 or 없음": False},
    ]
    s1_df_data = ans.get("step1_2_df", default_s1_df)
    s1_df = pd.DataFrame(s1_df_data)
    edited_step1_2_df = grid_editor(
        s1_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=disabled_flag,
        column_config={
            "구분": st.column_config.TextColumn("구분", width="medium"),
            "필수 서비스 항목": st.column_config.TextColumn("필수 서비스 항목", width="large"),
            "충분": st.column_config.CheckboxColumn("충분", width="small"),
            "부족 or 없음": st.column_config.CheckboxColumn("부족 or 없음", width="small"),
        },
        key=f"s1_2_editor_{k_sfx}",
        lock_cols={"구분": [d["구분"] for d in default_s1_df], "필수 서비스 항목": [d["필수 서비스 항목"] for d in default_s1_df]},
    )

    st.markdown("<br>**3. 선택한 지역의 핵심 문제점** *(반드시 실제 현장 답사 및 데이터에 기반한 내용을 작성할 것)*", unsafe_allow_html=True)
    col_p1, col_d1 = st.columns([1, 2])
    with col_p1:
        step1_p1 = st.text_area("문제점 1", value=ans.get("step1_p1", ans.get("step1_3_1", "")), height=100, disabled=disabled_flag, key=f"s1_p1_{k_sfx}")
    with col_d1:
        step1_d1 = st.text_area("데이터 1", value=ans.get("step1_d1", ""), height=100, disabled=disabled_flag, key=f"s1_d1_{k_sfx}")

    col_p2, col_d2 = st.columns([1, 2])
    with col_p2:
        step1_p2 = st.text_area("문제점 2", value=ans.get("step1_p2", ans.get("step1_3_2", "")), height=100, disabled=disabled_flag, key=f"s1_p2_{k_sfx}")
    with col_d2:
        step1_d2 = st.text_area("데이터 2", value=ans.get("step1_d2", ""), height=100, disabled=disabled_flag, key=f"s1_d2_{k_sfx}")

    col_p3, col_d3 = st.columns([1, 2])
    with col_p3:
        step1_p3 = st.text_area("문제점 3", value=ans.get("step1_p3", ans.get("step1_3_3", "")), height=100, disabled=disabled_flag, key=f"s1_p3_{k_sfx}")
    with col_d3:
        step1_d3 = st.text_area("데이터 3", value=ans.get("step1_d3", ""), height=100, disabled=disabled_flag, key=f"s1_d3_{k_sfx}")

    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ----------------------------------------------------
    # Step 2. 도시 개조 포인트를 활용한 트레이드오프 설계
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 2. 도시 개조 포인트를 활용한 트레이드오프 설계</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 20px;'>
        <b style='color: #2b6cb0;'>▶ 트레이드오프 설계</b><br>
        <span style='font-size: 13.5px; color: #4a5568;'>: 두 개 이상의 상충되는 요구사항(예: 성능 대 비용, 유연성 대 단순성) 사이에서 최선의 선택을 하기 위해 장단점을 저울질하고 조율하는 과정. 완벽한 설계는 존재하지 않으며, 모든 설계는 무엇인가를 얻는 대신 다른 것을 포기하는 구조를 가질 수 밖에 없음</span><br><br>
        <b style='color: #2b6cb0;'>▶ 도시 개조 포인트</b><br>
        <span style='font-size: 13.5px; color: #4a5568;'>
        : 기본 150포인트 부여, 포인트를 활용하여 기존의 비효율적, 차량 중심 공간을 보행자를 위한 친환경 인프라로!!<br>
        : 새롭게 추가하는 카테고리/코드/세부 개조 항목 관련한 포인트는 최소 10pt, 최대 20pt(10~20pt)<br>
        : 포인트는 남김 없이 모두 사용해야 함<br>
        : 최소한의 현실 가능성은 충족할 것 예) 지하철 개통, 공항 건설... ㅠ.ㅠ
        </span>
    </div>
    """, unsafe_allow_html=True)

    # ----------------------------------------------------
    # 1. [참고 카탈로그] 도시 개조 포인트 예시 카테고리 (학습지 1페이지 원본 서식)
    # ----------------------------------------------------
    with st.expander("📋 [참고] 도시 개조 포인트 및 카테고리 예시 카탈로그 (클릭하여 펼치기/접기)", expanded=True):
        st.markdown("""
        <table style="width: 100%; border-collapse: collapse; margin-bottom: 15px; font-size: 13.5px; background-color: #ffffff;">
            <thead>
                <tr style="background-color: #f1f5f9; text-align: center;">
                    <th style="padding: 9px; border: 1px solid #cbd5e1; width: 25%;">카테고리</th>
                    <th style="padding: 9px; border: 1px solid #cbd5e1; width: 10%;">코드</th>
                    <th style="padding: 9px; border: 1px solid #cbd5e1; width: 50%;">세부 개조 항목</th>
                    <th style="padding: 9px; border: 1px solid #cbd5e1; width: 15%;">비용</th>
                </tr>
            </thead>
            <tbody>
                <tr><td rowspan="6" style="text-align: center; vertical-align: middle; font-weight: bold; border: 1px solid #cbd5e1; background: #fafafa;">안전한 보행 환경</td><td style="text-align: center; border: 1px solid #cbd5e1;">A-1</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">여고생 안심 하교길 스마트 로드</td><td style="text-align: center; border: 1px solid #cbd5e1;">-15pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">A-2</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">아파트 단지 간 담장 철거 및 공공 보행로 연결</td><td style="text-align: center; border: 1px solid #cbd5e1;">-20pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">A-3</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">차로 축소 및 쾌적한 보행을 위한 녹지 공간 조성</td><td style="text-align: center; border: 1px solid #cbd5e1;">-20pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">A-4</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">스마트 횡단보도 및 교통약자/학생 쉼터</td><td style="text-align: center; border: 1px solid #cbd5e1;">-10pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">A-5</td><td style="border: 1px solid #cbd5e1;"></td><td style="text-align: center; border: 1px solid #cbd5e1;"></td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">A-6</td><td style="border: 1px solid #cbd5e1;"></td><td style="text-align: center; border: 1px solid #cbd5e1;"></td></tr>
                <tr><td rowspan="5" style="text-align: center; vertical-align: middle; font-weight: bold; border: 1px solid #cbd5e1; background: #fafafa;">녹지 및 생태공간 구축</td><td style="text-align: center; border: 1px solid #cbd5e1;">B-1</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">아파트 상가/방치 공터 → 도심 소공원 조성</td><td style="text-align: center; border: 1px solid #cbd5e1;">-15pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">B-2</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">도심 바람길 숲 및 수변 산책로 조성</td><td style="text-align: center; border: 1px solid #cbd5e1;">-15pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">B-3</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">에코 펫파크(반려견 전용 공원 및 산책로)</td><td style="text-align: center; border: 1px solid #cbd5e1;">-15pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">B-4</td><td style="border: 1px solid #cbd5e1;"></td><td style="text-align: center; border: 1px solid #cbd5e1;"></td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">B-5</td><td style="border: 1px solid #cbd5e1;"></td><td style="text-align: center; border: 1px solid #cbd5e1;"></td></tr>
                <tr><td rowspan="5" style="text-align: center; vertical-align: middle; font-weight: bold; border: 1px solid #cbd5e1; background: #fafafa;">문화와 교육을 위한 공간</td><td style="text-align: center; border: 1px solid #cbd5e1;">C-1</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">24시간 공공 스터디 & 커뮤니티 카페</td><td style="text-align: center; border: 1px solid #cbd5e1;">-15pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">C-2</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">청소년 팝업 스튜디오 & 소공연장</td><td style="text-align: center; border: 1px solid #cbd5e1;">-15pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">C-3</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">친환경 스마트 팜</td><td style="text-align: center; border: 1px solid #cbd5e1;">-10pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">C-4</td><td style="border: 1px solid #cbd5e1;"></td><td style="text-align: center; border: 1px solid #cbd5e1;"></td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">C-5</td><td style="border: 1px solid #cbd5e1;"></td><td style="text-align: center; border: 1px solid #cbd5e1;"></td></tr>
                <tr><td rowspan="4" style="text-align: center; vertical-align: middle; font-weight: bold; border: 1px solid #cbd5e1; background: #fafafa;">효율적인 교통과 모빌리티 구축</td><td style="text-align: center; border: 1px solid #cbd5e1;">D-1</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">공유 자전거 및 킥보드 전용 도로</td><td style="text-align: center; border: 1px solid #cbd5e1;">-15pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">D-2</td><td style="border: 1px solid #cbd5e1; padding: 6px 10px;">스마트 버스 쉘터(공기 청정, 냉난방 설비 구축)</td><td style="text-align: center; border: 1px solid #cbd5e1;">-10pt</td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">D-3</td><td style="border: 1px solid #cbd5e1;"></td><td style="text-align: center; border: 1px solid #cbd5e1;"></td></tr>
                <tr><td style="text-align: center; border: 1px solid #cbd5e1;">D-4</td><td style="border: 1px solid #cbd5e1;"></td><td style="text-align: center; border: 1px solid #cbd5e1;"></td></tr>
            </tbody>
        </table>
        """, unsafe_allow_html=True)

    # ----------------------------------------------------
    # 2. [학생 실제 작성 표] 도시 개조 트레이드오프 설계표 (순번 1~8)
    # ----------------------------------------------------
    st.markdown("##### 🛠️ 도시 개조 트레이드오프 설계표 (순번 1~8 작성 / 칸을 눌러 입력 / [➕ 줄 추가]·[➖ 마지막 줄 삭제] 버튼으로 줄 수 조절)")
    default_tradeoff_design = [
        {"순번": i, "선택 코드": "", "버릴 공간 -> 채울 인프라": "", "사용 포인트": "-15pt", "공간 재설계 이유 및 기대효과": ""}
        for i in range(1, 9)
    ]
    saved_tradeoff = ans.get("step2_tradeoff_df") or ans.get("step2_table_df") or default_tradeoff_design
    df_tradeoff = pd.DataFrame(saved_tradeoff)

    # 🌟 [자동 정렬] 학생이 행을 추가하더라도 '선택 코드'(A-1, A-2... B-1...) 순서대로 자동 정렬
    # (자동 정렬은 입력 도중 줄 순서가 바뀌는 문제를 일으켜 사용하지 않음 — 학생이 적은 순서 그대로 유지)

    # 🌟 [비용 옵션: 최소 10pt ~ 최대 30pt, 5pt 간격]
    point_options = ["-10pt", "-15pt", "-20pt", "-25pt", "-30pt"]
    code_options = [
        "A-1", "A-2", "A-3", "A-4", "A-5", "A-6",
        "B-1", "B-2", "B-3", "B-4", "B-5",
        "C-1", "C-2", "C-3", "C-4", "C-5",
        "D-1", "D-2", "D-3", "D-4",
        "자율추가"
    ]

    edited_step2_tradeoff_df = grid_editor(
        df_tradeoff,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=disabled_flag,
        column_config={
            "순번": st.column_config.NumberColumn("순번", width="small"),
            "선택 코드": st.column_config.TextColumn("선택 코드", width="small"),
            "버릴 공간 -> 채울 인프라": st.column_config.TextColumn("버릴 공간 -> 채울 인프라", width="large"),
            "사용 포인트": st.column_config.SelectboxColumn("사용 포인트", options=point_options, width="small"),
            "공간 재설계 이유 및 기대효과": st.column_config.TextColumn("공간 재설계 이유 및 기대효과", width="large"),
        },
        key=f"s2_tradeoff_design_{user_key if 'user_key' in locals() else current_user_key}"
    )

    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ----------------------------------------------------
    # 3. [완벽 복원] Step 3. N분 도시 공간 지도 스케치 (파일 업로드 & 즉시 확인)
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 3. N분 도시 공간 지도 스케치</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 20px;'>
        <b style='color: #2b6cb0;'>▶ 변경 전과 변경 후의 지도 스케치 (변경 인프라 및 보행선 관련 내용 표시)</b><br>
        <span style='font-size: 13.5px; color: #4a5568;'>
        • 모둠에서 직접 손으로 그리거나 태블릿으로 작업한 <b>지도 스케치 사진</b>을 촬영하여 등록하세요.<br>
        • 파일을 올리면 아래 화면에 즉시 등록 이미지가 나타납니다.
        </span>
    </div>
    """, unsafe_allow_html=True)

    col_map_before, col_map_after = st.columns(2)
    saved_map_before = ans.get("step3_map_before", "")
    saved_map_after = ans.get("step3_map_after", "")

    # [전] 변경 전 지도 스케치
    with col_map_before:
        st.markdown("#### 🗺️ [전] 변경 전 지도 스케치")
        current_map_before = take_upload("변경 전 스케치 파일 (JPG, PNG)", "up_map_before", process_sketch_image,
                                         type=["png", "jpg", "jpeg", "webp"], disabled=disabled_flag) or saved_map_before
        if current_map_before:
            st.image(current_map_before, caption="[변경 전] 등록된 지도 스케치", use_container_width=True)
        else:
            st.info("📷 등록된 '변경 전' 스케치가 없습니다.")

    # [후] 변경 후 지도 스케치
    with col_map_after:
        st.markdown("#### 🗺️ [후] 변경 후 지도 스케치")
        current_map_after = take_upload("변경 후 스케치 파일 (JPG, PNG)", "up_map_after", process_sketch_image,
                                        type=["png", "jpg", "jpeg", "webp"], disabled=disabled_flag) or saved_map_after
        if current_map_after:
            st.image(current_map_after, caption="[변경 후] 등록된 지도 스케치", use_container_width=True)
        else:
            st.info("📷 등록된 '변경 후' 스케치가 없습니다.")

    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)
    # ----------------------------------------------------
    # Step 4. 3분 공청회 발표를 위한 준비
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 4. 3분 공청회 발표를 위한 준비</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 20px;'>
        <b style='color: #2b6cb0;'>▶ 핵심 정책 슬로건과 발표 내용 요약</b><br>
        <span style='font-size: 13.5px; color: #4a5568;'>
        * STEP 1~3의 탐구 결과를 바탕으로, 발표 자료를 만들어 봅시다.<br>
        * 핵심 정책 슬로건에는 버릴공간과 문제점 + 채울 인프라와 미래 가치에 대한 내용이 반드시 들어가야 함.
        </span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("**1. 핵심 정책 슬로건**")
    step4_1 = st.text_input("1. 핵심 정책 슬로건", value=ans.get("step4_1", ""), label_visibility="collapsed", disabled=disabled_flag, key="s4_1_input")

    st.markdown("""
    <div style='text-align: center; font-weight: 800; font-size: 16px; background-color: #f8f9fa; border: 1px solid #dee2e6; padding: 10px; margin: 25px 0 15px 0; border-radius: 4px;'>
        연설 내용 구조화 스크립트 작성
    </div>
    """, unsafe_allow_html=True)

    st.markdown("**2. 실제 답사 및 데이터로 확인한 선택한 지역의 가장 심각한 공간 문제는 무엇이라고 생각하는가?**")
    step4_2 = st.text_area("2. 실제 답사 및 데이터로 확인한 문제", value=ans.get("step4_2", ""), height=100, label_visibility="collapsed", disabled=disabled_flag, key="s4_2_input")

    # 3. 버리고 채운 것
    st.markdown("**3. 한정된 150pt를 활용해 무엇을 버리고 무엇을 채웠는가?**")
    step4_3 = st.text_area("3. 버리고 채운 것", value=ans.get("step4_3", ""), height=100, label_visibility="collapsed", disabled=disabled_flag, key=f"s4_3_input_{user_key if 'user_key' in locals() else current_user_key}")

    # 4. 그 이유 (NameError를 일으켰던 핵심 변수 정의)
    st.markdown("**4. 그 이유는 무엇인가?**")
    step4_4_reason = st.text_area("4. 그 이유", value=ans.get("step4_4_reason", ans.get("step4_3_reason", "")), height=100, label_visibility="collapsed", disabled=disabled_flag, key=f"s4_4_reason_input_{user_key if 'user_key' in locals() else current_user_key}")

    # 5. 일상의 변화 (기존 4번 작성 내용 자동 승계)
    st.markdown("**5. 공간 재설계로 인해 일상이 어떻게 변화할 것이라고 생각하는가?**")
    step4_5 = st.text_area("5. 일상의 변화", value=ans.get("step4_5", ans.get("step4_4", "")), height=100, label_visibility="collapsed", disabled=disabled_flag, key=f"s4_5_input_{user_key if 'user_key' in locals() else current_user_key}")

    # ----------------------------------------------------
    # 저장하기 버튼 & 데이터베이스 동기화
    # ----------------------------------------------------
    st.markdown("<br>", unsafe_allow_html=True)

    saved_tradeoff_records = edited_step2_tradeoff_df.to_dict('records') if hasattr(edited_step2_tradeoff_df, 'to_dict') else []
    new_ans = {
        "m1_id": m1_id, "m1_name": m1_name, "m2_id": m2_id, "m2_name": m2_name,
        "m3_id": m3_id, "m3_name": m3_name, "m4_id": m4_id, "m4_name": m4_name,
        "step1_n_min": step1_n_min,
        "step1_1": step1_1,
        "step1_2_df": edited_step1_2_df.to_dict('records') if hasattr(edited_step1_2_df, 'to_dict') else ans.get("step1_2_df", []),
        "step1_p1": step1_p1, "step1_d1": step1_d1,
        "step1_p2": step1_p2, "step1_d2": step1_d2,
        "step1_p3": step1_p3, "step1_d3": step1_d3,
        "step2_tradeoff_df": saved_tradeoff_records,
        "step2_table_df": saved_tradeoff_records,
        "step2_custom_df": saved_tradeoff_records,
        "step2_point_df": saved_tradeoff_records,
        "step3_map_before": current_map_before,
        "step3_map_after": current_map_after,
        "step4_1": step4_1,
        "step4_2": step4_2,
        "step4_3": step4_3,
        "step4_4_reason": step4_4_reason,
        "step4_5": step4_5,
        "step4_4": step4_5
    }

    autosave_draft(user_key, category, new_ans, disabled_flag)  # 🛟 서버 임시저장
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        commit_activity(user_key, category, new_ans, u_name)

MF_GALLERY_ACT = "[갤러리] 2학년 미디어 파사드 피드백"

# 응답 탭(구글 시트)에 이 활동지를 보여 줄 순서·이름 (화면과 동일)
_S1, _S2, _S3 = "Step 1. 우리 지역의 정체성 탐색", "Step 2. 캔버스 선정", "Step 3. 주어진 조건 진단 및 대응 설계"
_S4, _S5, _S6 = "Step 4. 스토리보드 4컷 & 시뮬레이션", "Step 5. 작품 설명 카드", "Step 6. 제출 전 점검 및 활용 기록"
MF_SHEET_SPEC = [
    ("field", "", "학번", "ind_id"), ("field", "", "이름", "ind_name"), ("field", "", "희망 진로", "ind_career"),
    ("table", _S1, "", "step1_df"), ("field", _S1, "최종 선택 키워드", "step1_keyword"), ("field", _S1, "단 하나의 메시지", "step1_message"),
    ("table", _S2, "", "step2_matrix_df"), ("field", _S2, "최종 선정 건물", "step2_final_building"),
    ("field", _S2, "선정 이유", "step2_reason"), ("field", _S2, "현장 사진/로드뷰 캡처", "step2_site_photo"),
    ("table", _S3, "", "step3_df"),
    ("table", _S4, "", "step4_df"), ("field", _S4, "작품 전체 길이(초)", "step4_total_sec"), ("field", _S4, "시뮬레이션 영상", "step4_video"), ("field", _S4, "직접 만든 영상 링크", "step4_video_link"),
    ("field", _S5, "작품 제목", "step5_title"), ("field", _S5, "전시 장소", "step5_place"), ("field", _S5, "작품 개요", "step5_desc"),
    ("field", _S5, "지역 정체성 반영", "step5_identity"), ("field", _S5, "현장 조건 반영", "step5_condition"),
    ("field", _S5, "변화 - 관람객 측면", "step5_change_visitor"), ("field", _S5, "변화 - 주민 측면", "step5_change_resident"),
    ("field", _S5, "변화 - 상권 측면", "step5_change_market"),
    ("table", _S6, "", "step6_ai_df"), ("field", _S6, "활동 성찰", "step6_reflection"),
    ("table", _S6 + " › 자동 점검 결과", "", "step6_chk_df"),
]
MF_TECH_OPTIONS = ["프로젝션 매핑", "LED 미디어월", "레이저·라이트 쇼", "인터랙티브(관람객 반응)", "AR(증강현실)", "사운드 연동", "기타"]
# 기술 이름: (한 줄 설명, 만들 수 있는 효과, 이런 장면에 어울림, 고려할 점)
MF_TECH_INFO = {
    "프로젝션 매핑": ("프로젝터로 건물 벽에 영상을 비추되, 창문·기둥·모서리 같은 건물의 생김새에 딱 맞춰 영상을 '입히는' 기술",
                  "건물이 무너지거나 움직이는 착시, 벽이 물·나무·철 같은 다른 재질로 바뀌는 변신, 창문 하나하나에 서로 다른 장면 넣기",
                  "건물 자체가 주인공이 되는 장면, 도입부의 '건물이 깨어나는' 연출",
                  "어두워야 잘 보임(야간 전용), 주변 간판·가로등 빛이 강하면 흐려짐, 프로젝터를 설치할 맞은편 공간 필요"),
    "LED 미디어월": ("건물 외벽에 LED 화면(대형 전광판)을 붙여 영상을 직접 띄우는 기술",
                  "낮에도 밝고 선명한 색, 모서리 화면을 이용해 화면 밖으로 튀어나오는 듯한 입체 착시(아나모픽) 연출",
                  "선명한 색감이 중요한 장면, 시간대별로 바뀌는 상시 콘텐츠",
                  "설치 비용이 큼, 너무 밝으면 빛공해·눈부심, 주거지와 가까우면 민원 가능 → 밝기·운영 시간 조절이 대응 방안이 될 수 있음"),
    "레이저·라이트 쇼": ("레이저 빔과 무대 조명(서치라이트·무빙라이트)으로 빛줄기와 선을 벽면이나 하늘에 그리는 기술",
                   "하늘로 뻗는 빛기둥, 건물 윤곽을 따라 그려지는 선, 음악에 맞춰 깜빡이는 강렬한 리듬",
                   "분위기가 최고조에 이르는 절정 장면, 축제·기념 장면",
                   "눈에 직접 닿지 않게 해야 함(안전), 하늘 방향은 항공 안전 고려, 세밀한 그림 표현은 어려움"),
    "인터랙티브(관람객 반응)": ("센서·카메라·스마트폰으로 관람객의 움직임·소리·터치를 읽어 영상이 반응하게 하는 기술",
                         "관람객이 손을 흔들면 꽃이 피거나 새가 날아오름, 관람객 수에 따라 빛이 밝아짐, 휴대폰으로 보낸 메시지가 벽에 떠오름",
                         "관람객이 메시지를 '완성'하는 마무리 장면, 참여형 장면",
                         "장비와 운영이 복잡함, 관람객이 모일 안전한 공간·동선 필요"),
    "AR(증강현실)": ("스마트폰 카메라로 건물을 비추면 화면 속 실제 건물 위에 가상 영상이 겹쳐 보이게 하는 기술",
                  "실제로 빛을 쏘지 않고도 건물이 변하는 장면을 보여 줌, 사람마다 다른 장면 체험",
                  "주거지와 가까워 실제로 빛을 비추기 어려운 건물(Step 3의 빛공해·민원 조건에 대한 대응 방안)",
                  "앱이나 QR 코드가 필요함, 여럿이 함께 보는 감동은 줄어듦"),
    "사운드 연동": ("음악·효과음·내레이션을 영상의 장면 전환 타이밍에 맞춰 함께 들려주는 기술",
                "장면이 바뀌는 순간의 몰입감, 지역의 소리(파도, 공장 기계음, 새소리)로 정체성 강조",
                "모든 장면에 함께 쓰기 좋음, 특히 도입과 절정",
                "소음 민원 가능 → 헤드폰·QR 오디오 안내가 대응 방안이 될 수 있음"),
    "기타": ("위에 없는 기술(예: 물안개 스크린, 드론 라이트 쇼, 키네틱 조명 등)", "장면 설명에 어떤 기술인지 구체적으로 적어 주세요", "-", "-"),
}

def mf_quad_to_shape(q):
    """네 모서리(%) → 위치·폭·높이·기울임(슬라이더용)"""
    tlx, tly, trx, try_, brx, bry, blx, bly = [float(v) for v in q]
    tx, bx = (tlx + trx) / 2, (blx + brx) / 2
    ly, ry = (tly + bly) / 2, (try_ + bry) / 2
    return {"cx": (tx + bx) / 2, "cy": (ly + ry) / 2, "top_w": trx - tlx, "bot_w": brx - blx,
            "left_h": bly - tly, "right_h": bry - try_, "lean": tx - bx, "rise": ry - ly}

def mf_shape_to_quad(s):
    """위치·폭·높이·기울임 → 네 모서리(%)"""
    tx, bx = s["cx"] + s["lean"] / 2, s["cx"] - s["lean"] / 2
    ly, ry = s["cy"] - s["rise"] / 2, s["cy"] + s["rise"] / 2
    pts = [tx - s["top_w"] / 2, ly - s["left_h"] / 2, tx + s["top_w"] / 2, ry - s["right_h"] / 2,
           bx + s["bot_w"] / 2, ry + s["right_h"] / 2, bx - s["bot_w"] / 2, ly + s["left_h"] / 2]
    return [round(max(0.0, min(100.0, v)), 1) for v in pts]
MF_RUBRIC = [
    ("Step 1. 정체성 탐색", "출처가 있는 근거 자료로 지역 정체성 키워드를 찾았는가 / 메시지가 한 문장으로 분명한가", "예시 20"),
    ("Step 2. 캔버스 선정", "후보 3곳을 실제 확인(답사·로드뷰 캡처)하고 비교했는가 / 선정 이유가 Step 1과 연결되는가", "예시 15"),
    ("Step 3. 조건 진단·대응", f"{len(MF_S3_DEFAULTS)}개 조건을 사실로 확인했는가 / 제약을 작품의 조형 요소로 뒤집어 활용했는가", "예시 20"),
    ("Step 4. 스토리보드", "도입-전개-절정-마무리 흐름이 메시지와 연결되는가 / 기술·색감·시간이 구체적인가 / 건물 합성 시뮬레이션", "예시 25"),
    ("Step 5. 작품 설명 카드", "근거를 인용해 설명했는가 / 관람객·주민·상권 변화를 구체적으로 제시했는가", "예시 15"),
    ("Step 6. 성찰·AI 활용", "AI 활용을 정직하게 기록했는가 / 진로와 연결한 성찰이 있는가", "예시 5"),
]

def _mf_filled(v):
    return isinstance(v, str) and v.strip() not in ("", "None", "nan") or (not isinstance(v, str) and v not in (None, "") and not (isinstance(v, float) and v != v))

def _mf_auto_check(n):
    """현재 작성 내용으로 자동 점검: [(통과 여부, 항목, 부족한 점)]"""
    res = []
    s1 = [r for r in n.get("step1_df", []) if _mf_filled(r.get("내가 찾은 정체성 키워드 혹은 문장"))]
    miss1 = [str(r.get("구분", "")).strip() or "?" for r in s1
             if not (_mf_filled(r.get("근거가 되는 사실·통계·사건")) and _mf_filled(r.get("출처 (기관명 / 자료명 / 연도)")))]
    res.append((len(s1) >= 3 and not miss1, "Step 1 키워드 3개 이상 + 각각 근거·출처",
                f"키워드 {len(s1)}개" + (f" / 근거·출처 빈칸: {', '.join(miss1)}" if miss1 else "")))
    res.append((_mf_filled(n.get("step1_keyword")) and _mf_filled(n.get("step1_message")), "Step 1 최종 키워드·메시지", "빈칸 있음"))
    mx = {str(r.get("검토 항목", "")).split("\n")[0]: r for r in n.get("step2_matrix_df", [])}
    names = mx.get("건물명", {})
    cand = sum(1 for c in ("후보 1", "후보 2", "후보 3") if _mf_filled(names.get(c)))
    res.append((cand >= 3, "Step 2 후보 건물 3곳 비교", f"후보 {cand}곳 작성"))
    res.append((_mf_filled(n.get("step2_final_building")) and _mf_filled(n.get("step2_reason")), "Step 2 최종 건물·선정 이유", "빈칸 있음"))
    res.append((bool(n.get("step2_site_photo")), "Step 2 현장 사진/로드뷰 캡처 첨부", "사진 없음"))
    s3 = mf_normalize_ans({"step3_df": n.get("step3_df", [])})["step3_df"]     # 지운 조건 줄은 점검에서 제외
    miss3 = [str(r.get("조건 영역", "")).split("\n")[0] for r in s3
             if not (_mf_filled(r.get("현장의 실제 조건 (확인한 사실)")) and _mf_filled(r.get("나의 대응 방안")))]
    res.append((len(s3) >= len(MF_S3_DEFAULTS) and not miss3, f"Step 3 조건 {len(MF_S3_DEFAULTS)}개 모두 '실제 조건 + 대응 방안'",
                ("빈칸: " + ", ".join(miss3)) if miss3 else f"{len(s3)}개"))
    cuts = n.get("step4_df", [])
    no_img = [c.get("컷", "") for c in cuts if not c.get("스케치_img")]
    no_desc = [c.get("컷", "") for c in cuts if not _mf_filled(c.get("장면 설명"))]
    res.append((len(cuts) == 4 and not no_img, "Step 4 스케치 4컷", ("없음: " + ", ".join(no_img)) if no_img else ""))
    res.append((not no_desc, "Step 4 장면 설명 4컷", ("없음: " + ", ".join(no_desc)) if no_desc else ""))
    res.append((bool(_media_ok(n.get("step4_video"))) or str(n.get("step4_video_link", "")).startswith("http"), "Step 4 시뮬레이션 영상(자동 생성 또는 링크)", "영상 없음"))
    s5 = ["step5_title", "step5_place", "step5_desc", "step5_identity", "step5_condition",
          "step5_change_visitor", "step5_change_resident", "step5_change_market"]
    miss5 = sum(1 for k in s5 if not _mf_filled(n.get(k)))
    res.append((miss5 == 0, "Step 5 작품 설명 카드 8칸", f"빈칸 {miss5}개"))
    ai_bad = [i + 1 for i, r in enumerate(n.get("step6_ai_df", []))
              if any(_mf_filled(v) for v in r.values()) and not all(_mf_filled(v) for v in r.values())]
    res.append((not ai_bad, "AI 활용 기록(쓴 줄은 세 칸 모두)", ("미완성 줄: " + ", ".join(map(str, ai_bad))) if ai_bad else ""))
    res.append((_mf_filled(n.get("step6_reflection")), "활동 성찰", "빈칸"))
    return res

def _mf_table_titles(cols):
    """긴 열 제목은 짧게 보여 주고 설명은 마우스를 올리면 보이게(저장되는 이름은 그대로)"""
    short = {
        "구분": ("구분", None, "small"),
        "내가 찾은 정체성 키워드 혹은 문장": ("정체성 키워드·문장", "내가 찾은 정체성 키워드 혹은 문장", "medium"),
        "근거가 되는 사실·통계·사건": ("근거 (사실·통계·사건)", "근거가 되는 사실·통계·사건", "medium"),
        "출처 (기관명 / 자료명 / 연도)": ("출처", "기관명 / 자료명 / 연도 — 비어 있으면 점수로 인정되지 않습니다", "medium"),
        "조건 영역": ("조건 영역", None, "small"),
        "현장의 실제 조건 (확인한 사실)": ("실제 조건(확인한 사실)", "현장에서 확인한 사실", "medium"),
        "작품에 미치는 영향": ("작품에 미치는 영향", None, "medium"),
        "나의 대응 방안": ("나의 대응 방안", "조건을 작품 요소로 뒤집어 활용하면 가장 높은 평가", "medium"),
        "검토 항목": ("검토 항목", None, "small"),
    }
    cfg = {}
    for c in cols:
        if c in short:
            t, h, w = short[c]
            cfg[c] = st.column_config.TextColumn(t, help=h, width=w)
        elif str(c).startswith("후보"):
            cfg[c] = st.column_config.TextColumn(c, width="medium")
    return cfg


def render_activity3_2nd(user_key, u_info, current_role):
    category = ACT_2_3
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    ans = apply_draft(user_key, category, ans, enabled=(owner_key == user_key))
    ans = _clean_nan(ans)

    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    if current_role == "관리자":
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. (수정 가능)")

    st.markdown("<h2 style='font-size: 26px; font-weight: 900; color: #111; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ [2학년] 수행평가 3 - 도시 미디어 파사드 기획 및 스토리보드 제작</h2>", unsafe_allow_html=True)
    if current_role == "학생":
        if disabled_flag: st.error(status_msg, icon="🔒")
        else: st.success(status_msg, icon="🔓")

    with st.expander("📋 평가 기준 미리 보기 (무엇을 보고 채점하나요?)", expanded=False):
        st.markdown("| 단계 | 확인하는 것 | 배점 |\n|---|---|---|\n" + "\n".join(f"| {a} | {b} | {c} |" for a, b, c in MF_RUBRIC))
        st.caption("※ 배점은 예시이며 담당 선생님이 안내한 기준을 따릅니다. 맨 아래 '제출 전 자동 점검'에서 빠진 부분을 확인할 수 있습니다.")

    c1, c2, c3 = st.columns([1, 1, 2])
    ind_id = c1.text_input("학번", value=ans.get("ind_id", u_id), disabled=disabled_flag, key="s3_ind_id")
    ind_name = c2.text_input("이름", value=ans.get("ind_name", u_name), disabled=disabled_flag, key="s3_ind_name")
    ind_career = c3.text_input("희망 진로", value=ans.get("ind_career", ""), disabled=disabled_flag, key="s3_ind_career")
    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ---------------- Step 1 ----------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 1. 우리 지역의 정체성 탐색</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 15px;'>
        <b style='color: #2b6cb0;'>▶ 우리 지역의 정체성은 '느낌'이 아니라 '근거'에서 출발합니다. 키워드를 뽑고, 반드시 출처가 있는 자료로 뒷받침하세요.</b><br>
        <span style='font-size: 13.5px; color: #4a5568;'>
        ▶ <b>추천 검색어:</b> '울산광역시 통계포털', 'KOSIS 지역별 고용조사', '국가문화유산포털', '카카오맵/네이버맵 지적편집도'<br>
        <span style='color: #c53030; font-weight: bold;'>※ 근거 자료와 출처가 비어 있는 칸은 점수로 인정되지 않습니다. (열 제목에 마우스를 올리면 설명이 보입니다)</span>
        </span>
    </div>""", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #fefce8; border: 1px solid #fde047; border-left: 4px solid #ca8a04; border-radius: 6px; padding: 10px 14px; font-size: 14.5px; color: #1e293b; line-height: 1.6; margin-bottom: 12px;'>
        <b style='color: #b45309;'>💡 [작성 예시]</b> 구분: 자연·생태 | 키워드: 죽음의 강에서 되살아난 태화강<br>
        근거: 수질이 생태 등급으로 회복, 철새 서식지로 지정 | 출처: 울산시 환경 관련 통계 / 20OO
    </div>""", unsafe_allow_html=True)
    s1_cols = ["구분", "내가 찾은 정체성 키워드 혹은 문장", "근거가 되는 사실·통계·사건", "출처 (기관명 / 자료명 / 연도)"]
    default_s1_df = [{"구분": g, s1_cols[1]: "", s1_cols[2]: "", s1_cols[3]: ""} for g in ("1. 자연·생태", "2. 산업·경제", "3. 역사·문화")]
    s1_data = ans.get("step1_df") or default_s1_df
    s1_df = pd.DataFrame(s1_data)
    for c in s1_cols:
        if c not in s1_df.columns: s1_df[c] = ""
    s1_df = s1_df[s1_cols].fillna("")
    edited_step1_df = grid_editor(s1_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag,
                                     column_config=_mf_table_titles(s1_cols), key="s1_df_editor_2_3",
                                     lock_cols={"구분": [d["구분"] for d in default_s1_df]})

    st.markdown("<br>**내가 최종 선택한 핵심 키워드 혹은 문장**", unsafe_allow_html=True)
    step1_keyword = st.text_input("최종 선택 키워드", value=ans.get("step1_keyword", ""), label_visibility="collapsed", disabled=disabled_flag, key="s1_keyword_2_3")
    st.markdown("**내 작품이 전할 단 하나의 메시지** *(한 문장. 예: \"울산은 공장의 도시가 아니라, 공장과 철새가 함께 사는 도시다.\")*")
    step1_message = st.text_area("단 하나의 메시지", value=ans.get("step1_message", ""), height=80, label_visibility="collapsed", disabled=disabled_flag, key="s1_message_2_3")
    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ---------------- Step 2 ----------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 2. 캔버스 선정 — 어떤 건축물에 어떤 형태의 빛을 입힐 것인가?</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 15px;'>
        <span style='font-size: 13.5px; color: #4a5568;'>
        ▶ 우리 지역의 실제 건축물·구조물 3 곳을 후보로 조사하고 비교한 뒤 최종 1 곳을 선정합니다.<br>
        ▶ 직접 답사하거나 지도 로드뷰로 확인한 내용을 적습니다. <b>상상으로 쓴 내용은 인정되지 않습니다.</b><br>
        ▶ 최종 건물의 <b>현장 사진 또는 로드뷰 캡처</b>를 아래에 첨부하세요. Step 4의 '건물 합성 시뮬레이션'에 그대로 쓰입니다.
        </span>
    </div>""", unsafe_allow_html=True)
    s2_items = MF_S2_ITEMS          # 항목 이름은 파일 위쪽의 MF_S2_ITEMS 에서 고칩니다
    s2_data = mf_normalize_ans({"step2_matrix_df": ans.get("step2_matrix_df") or [{"검토 항목": it, "후보 1": "", "후보 2": "", "후보 3": ""} for it in s2_items]})["step2_matrix_df"]
    star_row = next((r for r in s2_data if str(r.get("검토 항목", "")).startswith("적합도")), None)
    table_rows = [r for r in s2_data if r is not star_row]
    s2_df = pd.DataFrame(table_rows).fillna("")
    edited_step2_df = grid_editor(s2_df, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag,
                                     column_config=_mf_table_titles(list(s2_df.columns)), key="s2_matrix_editor_2_3",
                                     lock_cols={"검토 항목": "all"})
    st.markdown("**후보별 적합도** *(별 1~5개)*")
    sc = st.columns(3)
    stars = {}
    for i, cand in enumerate(("후보 1", "후보 2", "후보 3")):
        prev = str((star_row or {}).get(cand, "")).count("★")
        with sc[i]:
            stars[cand] = _star_picker(f"{cand} 적합도", f"s2_starfb_{i}", prev, disabled_flag)
    s2_records = edited_step2_df.to_dict("records") + [{"검토 항목": "적합도 (☆ 5점 만점)", **{c: "★" * v + "☆" * (5 - v) for c, v in stars.items()}}]

    st.markdown("<br>**최종 선정 건물**", unsafe_allow_html=True)
    step2_final_building = st.text_input("최종 선정 건물", value=ans.get("step2_final_building", ""), label_visibility="collapsed", disabled=disabled_flag, key="s2_final_bldg")
    st.markdown("**이유** *(Step 1 의 정체성 키워드 혹은 문장과 연결하여 서술할 것)*")
    step2_reason = st.text_area("선정 이유", value=ans.get("step2_reason", ""), height=90, label_visibility="collapsed", disabled=disabled_flag, key="s2_reason_input")
    st.markdown("**최종 건물 현장 사진 / 로드뷰 캡처** *(정면이 잘 보이는 사진 1장)*")
    ph1, ph2 = st.columns([1, 1])
    with ph1:
        up_site_val = take_upload("현장 사진 또는 로드뷰 캡처 (JPG/PNG)", "s2_site_photo_up", lambda f: process_sketch_image(f, max_width=1000),
                                  type=["png", "jpg", "jpeg", "webp"], disabled=disabled_flag)
    site_photo = up_site_val or ans.get("step2_site_photo", "")
    with ph2:
        if site_photo: st.image(site_photo, caption="최종 선정 건물", use_container_width=True)
        else: st.caption("📷 아직 첨부한 사진이 없습니다.")
    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ---------------- Step 3 ----------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 3. 주어진 조건 진단 및 대응 설계</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 15px;'>
        <span style='font-size: 13.5px; color: #4a5568;'>
        ▶ 내가 바꿀 수 없는 조건을 받아들인 상태에서 작품을 어떻게 성립시킬지 설계합니다.<br>
        <b style='color: #c53030;'>※ 조건을 '문제점'으로만 적고 끝내면 점수를 받지 못합니다. 반드시 「나의 대응 방안」까지 채워야 합니다.</b><br>
        <span style='color: #2b6cb0;'>※ 제약을 오히려 작품의 조형 요소로 뒤집어 활용한 경우 가장 높은 평가를 받습니다.</span>
        </span>
    </div>""", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #fefce8; border: 1px solid #fde047; border-left: 4px solid #ca8a04; border-radius: 6px; padding: 10px 14px; font-size: 14.5px; color: #1e293b; line-height: 1.6; margin-bottom: 12px;'>
        <b style='color: #b45309;'>💡 [작성 예시]</b> 물리적 조건 | 실제 조건: 외벽의 40%가 창문이라 영상이 끊겨 보인다<br>
        영향: 인물이나 글자를 크게 넣으면 형태가 깨진다 | 대응: 창틀 격자를 공장 창문으로 역이용해 격자 사이로 빛이 번지는 산업 도시 이미지를 연출한다
    </div>""", unsafe_allow_html=True)
    s3_cols = ["조건 영역", "현장의 실제 조건 (확인한 사실)", "작품에 미치는 영향", "나의 대응 방안"]
    s3_defaults = MF_S3_DEFAULTS    # 조건 이름은 파일 위쪽의 MF_S3_DEFAULTS 에서 고칩니다
    s3_data = mf_normalize_ans({"step3_df": list(ans.get("step3_df") or [])})["step3_df"]   # 지운 조건 줄 제외 + 번호·이름 정리
    for d in s3_defaults[len(s3_data):]:          # 예전에 줄을 지웠어도 모든 조건이 보이도록 보충
        s3_data.append({"조건 영역": d, s3_cols[1]: "", s3_cols[2]: "", s3_cols[3]: ""})
    s3_df = pd.DataFrame(s3_data)
    for c in s3_cols:
        if c not in s3_df.columns: s3_df[c] = ""
    s3_df = s3_df[s3_cols].fillna("")
    edited_step3_df = grid_editor(s3_df, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag,
                                     column_config=_mf_table_titles(s3_cols), key="s3_editor_2_3",
                                     lock_cols={"조건 영역": "all"})
    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ---------------- Step 4 ----------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 4. 작품 스토리보드 4컷 & 미디어 파사드 시뮬레이션</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 15px;'>
        <span style='font-size: 13.5px; color: #4a5568;'>
        ▶ 도입-전개-절정-마무리 4컷마다 <b>스케치</b>와 <b>장면 설명·사용 기술·색감·소요 시간</b>을 작성합니다.<br>
        ▶ Step 2에서 건물 사진을 첨부했다면, 스케치를 <b>실제 건물 벽면에 비춘 모습</b>으로 합성해 볼 수 있습니다.
        </span>
    </div>""", unsafe_allow_html=True)
    with st.expander("💡 사용 기술 알아보기 — 각 기술로 어떤 장면을 만들 수 있나요?", expanded=False):
        st.markdown("| 기술 | 무엇인가요 | 만들 수 있는 효과 | 이런 장면에 어울려요 | 고려할 점 |\n|---|---|---|---|---|\n" +
                    "\n".join(f"| **{k}** | {v[0]} | {v[1]} | {v[2]} | {v[3]} |" for k, v in MF_TECH_INFO.items() if k != "기타"))
        st.caption("※ 기술의 '고려할 점'은 Step 3의 조건 진단과 연결해 보세요. 예: 주거지와 가까운 건물 → LED 밝기·운영 시간 조절, AR로 대체 등")
    cut_names = ["1 도입", "2 전개", "3 절정", "4 마무리"]
    saved_step4 = ans.get("step4_df", []) or []
    step4_data, current_images, secs = [], [], []
    for i, c_name in enumerate(cut_names):
        old = saved_step4[i] if i < len(saved_step4) and isinstance(saved_step4[i], dict) else {}
        old_scene = old.get("장면 설명", old.get("장면 설명 · 사용 기술 · 소요 시간", "") if "사용 기술" not in old else "")
        old_tech = [t for t in str(old.get("사용 기술", "")).split(", ") if t in MF_TECH_OPTIONS]
        st.markdown(f"#### 🎬 {c_name}")
        col_img, col_txt = st.columns([1, 1.2])
        with col_img:
            current_img = take_upload(f"[{c_name}] 스케치 파일 (JPG/PNG)", f"step4_file_{i}", process_sketch_image,
                                      type=["png", "jpg", "jpeg", "webp"], disabled=disabled_flag) or old.get("스케치_img", "")
            current_images.append(current_img)
            if current_img: st.image(current_img, caption=f"{c_name} 스케치", use_container_width=True)
            else: st.caption("📷 등록된 스케치 이미지가 없습니다.")
        with col_txt:
            scene = st.text_area(f"[{c_name}] 장면 설명", value=old_scene, height=110, key=f"step4_desc_{i}", disabled=disabled_flag,
                                 placeholder="이 장면에서 벽면에 무엇이 나타나고 어떻게 움직이는지, Step 1 메시지와 어떻게 이어지는지")
            tech = st.multiselect(f"[{c_name}] 사용 기술", MF_TECH_OPTIONS, default=old_tech, key=f"step4_tech_{i}", disabled=disabled_flag,
                                  help="여러 개를 함께 고를 수 있습니다. 각 기술의 효과는 위 '💡 사용 기술 알아보기'에서 자세히 볼 수 있습니다.")
            for tn in tech:
                info = MF_TECH_INFO.get(tn)
                if info: st.caption(f"✨ **{tn}**: {info[1]}")
            t1, t2 = st.columns([1.6, 1])
            color = t1.text_input(f"[{c_name}] 주요 색감·분위기", value=old.get("주요 색감", ""), key=f"step4_color_{i}", disabled=disabled_flag, placeholder="예: 새벽빛 푸른색 → 금빛")
            try: old_sec = int(float(old.get("소요 시간(초)", 0) or 0))
            except Exception: old_sec = 0
            sec = t2.number_input(f"[{c_name}] 소요 시간(초)", min_value=0, max_value=300, value=old_sec, step=1, key=f"step4_sec_{i}", disabled=disabled_flag)
        secs.append(sec)
        st.markdown("<hr style='margin: 12px 0; border: 0.5px dashed #cbd5e1;'>", unsafe_allow_html=True)
        combined = "\n".join(x for x in [scene, f"[사용 기술] {', '.join(tech)}" if tech else "", f"[색감] {color}" if color else "", f"[소요 시간] {sec}초" if sec else ""] if x)
        step4_data.append({"컷": c_name, "스케치_img": current_img, "장면 설명": scene, "사용 기술": ", ".join(tech),
                           "주요 색감": color, "소요 시간(초)": sec, "장면 설명 · 사용 기술 · 소요 시간": combined})
    total = sum(secs)
    st.markdown(f"**⏱️ 작품 전체 길이: {total}초** " + ("(소요 시간을 입력하세요)" if total == 0 else f"= {total // 60}분 {total % 60}초"))

    # 건물 합성 설정
    st.markdown("#### 🏙️ 건물 합성 시뮬레이션 — 내 스케치를 실제 건물 벽면에 비춰 보기")
    facade = dict(MF_DEFAULT_FACADE, **(ans.get("step4_facade") or {}))
    register_gif = None
    if not site_photo:
        st.info("ℹ️ Step 2에 최종 건물 사진을 첨부하면, 스케치를 건물 벽면에 비춘 모습으로 합성할 수 있습니다.")
    else:
        ev = st.session_state.get("mf_drag_editor")
        if isinstance(ev, dict) and ev.get("q") and len(ev["q"]) == 8 and ev.get("nonce") != st.session_state.get("_mf_drag_applied") and not disabled_flag:
            st.session_state["_mf_drag_applied"] = ev.get("nonce")
            if ev.get("mask"): st.session_state[f"mf_mask::{user_key}"] = ev["mask"]
            shp = mf_quad_to_shape(ev["q"])
            st.session_state["mf_mode"] = "네 모서리"      # 마우스로 맞추면 '네 모서리' 방식으로 전환
            for k_, f_, lo_, hi_ in [("mf_s_cx", "cx", 0, 100), ("mf_s_cy", "cy", 0, 100), ("mf_s_tw", "top_w", 2, 100), ("mf_s_bw", "bot_w", 2, 100),
                                     ("mf_s_lh", "left_h", 2, 100), ("mf_s_rh", "right_h", 2, 100), ("mf_s_lean", "lean", -50, 50), ("mf_s_rise", "rise", -50, 50)]:
                st.session_state[k_] = int(max(lo_, min(hi_, round(shp[f_]))))
        mouse_mode = str(st.session_state.get("mf_preview_mode", "")).startswith("🖱️")   # 마우스로 맞추는 중이면 위치 바는 잠금
        fa, fb = st.columns([1, 1.4])
        with fa:
            facade["mode"] = st.radio("벽면 맞추기 방식", ["사각형", "네 모서리"], **({} if "mf_mode" in st.session_state else {"index": 1 if facade.get("mode") == "네 모서리" else 0}), horizontal=True,
                                      format_func=lambda m: "▭ 반듯한 사각형" if m == "사각형" else "◇ 네 모서리 맞추기 (비스듬한 벽)", key="mf_mode", disabled=disabled_flag)
            if facade["mode"] == "사각형":
                st.caption("빛을 비출 벽면 위치를 맞추세요 (사진 기준 %)")
                if mouse_mode:
                    st.caption("🖱️ '마우스로 직접 맞추기' 중이라 위치·크기 바가 잠겨 있습니다.")
                facade["l"] = st.slider("왼쪽 시작 위치", 0, 95, int(facade["l"]), key="mf_l", disabled=disabled_flag or mouse_mode)
                facade["t"] = st.slider("위쪽 시작 위치", 0, 95, int(facade["t"]), key="mf_t", disabled=disabled_flag or mouse_mode)
                facade["w"] = st.slider("벽면 너비", 5, 100, int(facade["w"]), key="mf_w", disabled=disabled_flag or mouse_mode)
                facade["h"] = st.slider("벽면 높이", 5, 100, int(facade["h"]), key="mf_h", disabled=disabled_flag or mouse_mode)
                facade["fit"] = st.radio("스케치 맞추기", ["꽉 채우기", "비율 유지"], index=0 if facade.get("fit") != "비율 유지" else 1, horizontal=True, key="mf_fit", disabled=disabled_flag)
            else:
                st.caption("오른쪽 사진의 노란 테두리가 실제 벽면과 겹치도록 ① 위치 → ② 크기 → ③ 기울기 순서로 맞추세요. (사진 크기 기준 %)")
                sh = mf_quad_to_shape(facade.get("q") or mf_rect_to_quad(facade))
                ri = lambda v, lo, hi: int(max(lo, min(hi, round(v))))
                st.markdown("**① 위치** — 빛 영역 전체를 옮깁니다")
                if mouse_mode:
                    st.caption("🖱️ 지금은 '마우스로 직접 맞추기' 중이라 위치·크기·기울기 바가 모두 잠겨 있습니다. 오른쪽 화면에서 노란 영역 안쪽을 끌어 옮기고, 빨간 점 1~4를 끌어 모서리를 맞추세요. 바로 조절하려면 미리보기 방식을 '한 컷씩 보기'로 바꾸세요.")
                p1, p2 = st.columns(2)
                sh["cx"] = p1.slider("↔ 좌우 위치", 0, 100, key="mf_s_cx", **_sv("mf_s_cx", ri(sh["cx"], 0, 100)), disabled=disabled_flag or mouse_mode, help="0이면 사진 왼쪽 끝, 100이면 오른쪽 끝")
                sh["cy"] = p2.slider("↕ 상하 위치", 0, 100, key="mf_s_cy", **_sv("mf_s_cy", ri(sh["cy"], 0, 100)), disabled=disabled_flag or mouse_mode, help="0이면 사진 위쪽 끝, 100이면 아래쪽 끝")
                st.markdown("**② 크기** — 폭과 높이를 맞춥니다")
                w1, w2 = st.columns(2)
                sh["top_w"] = w1.slider("⬆ 윗변 폭", 2, 100, key="mf_s_tw", **_sv("mf_s_tw", ri(sh["top_w"], 2, 100)), disabled=disabled_flag or mouse_mode,
                                        help="높은 건물을 올려다 찍어 위로 갈수록 좁아 보이면 윗변 폭을 줄이세요.")
                sh["bot_w"] = w2.slider("⬇ 아랫변 폭", 2, 100, key="mf_s_bw", **_sv("mf_s_bw", ri(sh["bot_w"], 2, 100)), disabled=disabled_flag or mouse_mode)
                h1, h2 = st.columns(2)
                sh["left_h"] = h1.slider("⬅ 왼쪽 높이", 2, 100, key="mf_s_lh", **_sv("mf_s_lh", ri(sh["left_h"], 2, 100)), disabled=disabled_flag or mouse_mode,
                                         help="벽을 비스듬히 찍으면 멀리 있는 쪽이 작아 보입니다. 먼 쪽의 높이를 줄이세요.")
                sh["right_h"] = h2.slider("➡ 오른쪽 높이", 2, 100, key="mf_s_rh", **_sv("mf_s_rh", ri(sh["right_h"], 2, 100)), disabled=disabled_flag or mouse_mode,
                                          help="벽을 비스듬히 찍으면 멀리 있는 쪽이 작아 보입니다. 먼 쪽의 높이를 줄이세요.")
                st.markdown("**③ 기울기** — 기울어진 벽에 맞춥니다")
                t1, t2 = st.columns(2)
                sh["lean"] = t1.slider("↗ 위쪽 좌우 기울임", -50, 50, key="mf_s_lean", **_sv("mf_s_lean", ri(sh["lean"], -50, 50)), disabled=disabled_flag or mouse_mode,
                                       help="+로 올리면 윗변이 오른쪽으로, -로 내리면 왼쪽으로 기울어집니다.")
                sh["rise"] = t2.slider("⤵ 오른쪽 오르내림", -50, 50, key="mf_s_rise", **_sv("mf_s_rise", ri(sh["rise"], -50, 50)), disabled=disabled_flag or mouse_mode,
                                       help="+로 올리면 오른쪽 변 전체가 아래로, -로 내리면 위로 이동합니다.")
                facade["q"] = mf_shape_to_quad(sh)
                st.info("📐 **이럴 땐 이렇게!**  \n"
                        "• 높은 건물을 **올려다 찍은 사진** → '윗변 폭'을 아랫변보다 줄이세요.  \n"
                        "• 벽을 **오른쪽에서 비스듬히 찍은 사진** → 멀리 있는 '왼쪽 높이'를 줄이고, '오른쪽 오르내림'으로 윗변·아랫변 기울기를 맞추세요.  \n"
                        "• 맞출 때는 오른쪽 '한 컷씩 보기'로 보면 더 빠르게 확인할 수 있습니다.")
            # ✂️ 사각형이 아닌 건물(ㄴ자·ㄱ자 등): 건물 모양대로 따내기
            mk = f"mf_mask::{user_key}"
            if mk not in st.session_state: st.session_state[mk] = facade.get("mask")
            mask_on = st.checkbox("✂️ 건물 모양대로 따내기 (ㄴ자·ㄱ자처럼 사각형이 아닌 벽면)", key="mf_mask_on", disabled=disabled_flag,
                                  on_change=_mf_on_mask_toggle, **_sv("mf_mask_on", bool(facade.get("mask"))),
                                  help="켜면 오른쪽 '마우스로 직접 맞추기' 화면에 파란 선이 생깁니다. 파란 선을 건물 모양에 맞추면 그 안쪽에만 빛이 비춰집니다.")
            if mask_on:
                if not st.session_state.get(mk):
                    qq = facade["q"] if facade.get("mode") == "네 모서리" and facade.get("q") else mf_rect_to_quad(facade)
                    st.session_state[mk] = [[qq[0], qq[1]], [qq[2], qq[3]], [qq[4], qq[5]], [qq[6], qq[7]]]
                facade["mask"] = st.session_state[mk]
                mb1, mb2 = st.columns(2)
                mb1.button("📐 노란 틀을 건물 모양 크기에 맞추기", key="mf_fit_frame", on_click=_mf_fit_frame_to_mask, args=(mk,), disabled=disabled_flag,
                           help="정면에서 찍은 사진이면 이 버튼 하나로 틀이 맞춰집니다. 비스듬히 찍은 사진은 빨간 점으로 원근을 맞추세요.")
                mb2.button("↺ 모양 처음부터 다시", key="mf_reset_mask", on_click=_mf_reset_mask, args=(mk,), disabled=disabled_flag)
                st.caption(f"✂️ 건물 모양 꼭짓점 {len(facade['mask'])}개 · 오른쪽 화면에서 파란 선을 다듬으세요.")
            else:
                facade["mask"] = None
            facade["dark"] = st.slider("밤의 어두움", 0, 95, int(facade["dark"]), key="mf_dark", disabled=disabled_flag)
            facade["power"] = st.slider("빛의 세기", 10, 100, int(facade["power"]), key="mf_power", disabled=disabled_flag)
            show_guide = st.checkbox("빛 비출 영역 테두리·모서리 번호 표시", value=True, key="mf_guide")
        with fb:
            pv_options = [i for i, img in enumerate(current_images) if img]
            fkey = json.dumps(facade, sort_keys=True, ensure_ascii=False)
            view_opts = (["▶ 4컷 연속 재생"] if len(pv_options) >= 2 else []) + (["한 컷씩 보기"] if pv_options else []) + ["🖱️ 마우스로 직접 맞추기"]
            pv_mode = st.radio("미리보기 방식", view_opts, horizontal=True, key="mf_preview_mode")
            if pv_mode.startswith("🖱️"):
                base_img = mf_cached_composite(site_photo, current_images[pv_options[0]] if pv_options else "", fkey)
                cur_q = facade["q"] if facade.get("mode") == "네 모서리" and facade.get("q") else mf_rect_to_quad(facade)
                try:
                    mf_corner_editor(base_img, cur_q, disabled=disabled_flag, mask=facade.get("mask"), edit_mask=bool(facade.get("mask")))
                except Exception as _e:
                    st.warning(f"마우스 조절 화면을 불러오지 못했습니다. 왼쪽 슬라이더로 조절해 주세요. ({_e})")
                st.caption("모서리를 맞춘 뒤 '▶ 4컷 연속 재생'으로 바꾸면 작품 전체를 확인할 수 있습니다.")
            elif pv_options:
                if pv_mode.startswith("▶"):
                    with st.spinner("건물에 4컷을 이어서 비추는 중..."):
                        pv_gif = mf_cached_facade_gif(site_photo, tuple(current_images[i] for i in pv_options),
                                                      tuple((secs[i] or 3) for i in pv_options), fkey)
                    if pv_gif:
                        st.image(pv_gif, caption=" → ".join(cut_names[i] for i in pv_options) + " (건물 합성 연속 재생, 컷당 최대 5초)", use_container_width=True)
                        st.caption("슬라이더를 움직이면 바뀐 설정으로 다시 재생됩니다. 마음에 들면 아래 버튼으로 최종 영상에 등록하세요.")
                        if st.button("🎬 이 연속 재생 영상을 최종 시뮬레이션 영상으로 등록", key="mf_register_preview", disabled=disabled_flag):
                            register_gif = pv_gif
                else:
                    pv = st.radio("미리 볼 컷", pv_options, format_func=lambda i: cut_names[i], horizontal=True, key="mf_preview_cut")
                    comp = mf_cached_composite(site_photo, current_images[pv], fkey)
                    if comp: st.image(mf_draw_guide(comp, facade) if show_guide else comp, caption=f"{cut_names[pv]} — 건물 합성 미리보기", use_container_width=True)
            else:
                base_night = mf_cached_composite(site_photo, "", fkey)
                st.image(mf_draw_guide(base_night, facade) if show_guide else base_night, caption="스케치를 올리면 노란 테두리 안에 빛이 비춰집니다", use_container_width=True)

    st.markdown("#### 🎥 최종 미디어 파사드 시뮬레이션 영상")
    vkey = f"_mf_video::{user_key}"
    _raw_v = st.session_state.get(vkey) or ans.get("step4_video", "")
    saved_video = _raw_v if _media_ok(_raw_v) else ""          # 저장용 값(파일 참조 또는 data:…)
    if not saved_video and (_is_omitted(_raw_v) or str(_raw_v).startswith(MEDIA_REF)):
        st.warning("⚠️ 서버가 다시 시작되면서 등록했던 영상 파일이 지워졌습니다. [자동 생성]을 다시 누르거나 영상을 다시 올린 뒤 [저장하기]를 눌러 주세요.")
    if register_gif:
        saved_video = media_from_datauri(register_gif)
        st.session_state[vkey] = saved_video
        st.success("🎉 건물 합성 연속 재생 영상을 최종 시뮬레이션 영상으로 등록했습니다. 하단 [저장하기]를 눌러 반영하세요.")
    v_col1, v_col2 = st.columns([1, 1])
    with v_col1:
        st.markdown("**방법 A. 4컷 자동 연결 (컷 사이 부드러운 전환 + 컷별 소요 시간 반영)**")
        mode_opts = ["건물 합성 화면으로", "스케치만으로"] if site_photo else ["스케치만으로"]
        gif_mode = st.radio("영상 만들기 방식", mode_opts, horizontal=True, key="mf_gif_mode", disabled=disabled_flag)
        if st.button("🪄 4컷 연결 시뮬레이션 영상 자동 생성", key="btn_gen_gif", disabled=disabled_flag):
            pairs = [(img, s) for img, s in zip(current_images, secs) if img]
            if len(pairs) < 2:
                st.warning("⚠️ 최소 2개 이상의 스케치 이미지를 업로드해야 영상을 생성할 수 있습니다.")
            else:
                with st.spinner("시뮬레이션 영상을 만드는 중입니다..."):
                    frames = [mf_cached_composite(site_photo, img, json.dumps(facade, sort_keys=True, ensure_ascii=False)) if gif_mode.startswith("건물") else img for img, _ in pairs]
                    saved_video = media_from_datauri(make_facade_gif(frames, [s or 3 for _, s in pairs])) or saved_video
                    st.session_state[vkey] = saved_video
                st.success("🎉 시뮬레이션 영상이 만들어졌습니다! 하단 [저장하기]를 눌러 반영하세요. (미리보기는 컷당 최대 5초)")
    with v_col2:
        st.markdown("**방법 B. 직접 제작한 영상은 '링크'로 제출**")
        st.caption("영상 파일은 앱에 직접 올리지 않습니다. 학교 구글 드라이브(링크가 있는 사용자 보기 허용) 또는 유튜브(일부 공개)에 올린 뒤 주소를 붙여 넣으세요.")
        video_link = st.text_input("직접 만든 영상 링크 (구글 드라이브·유튜브 주소)", value=ans.get("step4_video_link", ""),
                                   key="step4_video_link", disabled=disabled_flag, placeholder="https://")
        video_link = (video_link or "").strip()
        if video_link and not video_link.startswith(("http://", "https://")):
            st.warning("⚠️ 주소는 https:// 로 시작해야 합니다. 주소창의 주소를 그대로 복사해 붙여 넣으세요.")
        elif video_link:
            if "youtu" in video_link:
                try: st.video(video_link)
                except Exception: st.markdown(f"[▶ 영상 열기]({video_link})")
            else:
                st.markdown(f"[▶ 제출한 영상 열어 보기]({video_link})  \n(새 창에서 열립니다. 선생님 계정에서 보이도록 공유 설정을 꼭 확인하세요)")
    saved_video = media_from_datauri(saved_video)              # 예전 방식(긴 data:…)으로 저장된 영상도 파일로 옮김
    if saved_video:
        st.session_state[vkey] = saved_video
        st.markdown("**▶ 등록된 시뮬레이션 영상 미리보기**")
        show_media(saved_video, caption="미디어 파사드 시뮬레이션", width=640)
    else:
        st.info("ℹ️ 아직 등록된 자동 생성 영상이 없습니다. [자동 생성] 버튼을 누르거나, 직접 만든 영상은 오른쪽에 링크로 제출하세요.")
    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ---------------- Step 5 ----------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 5. 작품 설명 카드 작성 및 갤러리 워크</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 20px;'>
        <span style='font-size: 13.5px; color: #4a5568;'>▶ 완성한 작품을 전시장에 걸 때 옆에 붙는 캡션 패널을 직접 씁니다. 선생님이 갤러리를 열면 같은 반 친구들의 작품을 감상하고 피드백을 남길 수 있습니다.</span>
    </div>""", unsafe_allow_html=True)
    st.markdown("**작품 제목** *(관람객의 눈길을 끌 수 있도록. 부제를 붙여도 좋다.)*")
    step5_title = st.text_input("작품 제목", value=ans.get("step5_title", ""), label_visibility="collapsed", disabled=disabled_flag, key="s5_title")
    st.markdown("**전시 장소** *(건물명 / 투사 벽면 / 권장 관람 위치)*")
    step5_place = st.text_input("전시 장소", value=ans.get("step5_place", step2_final_building), label_visibility="collapsed", disabled=disabled_flag, key="s5_place")
    st.markdown("**작품 개요** *(5 문장 이내)*")
    step5_desc = st.text_area("작품 개요", value=ans.get("step5_desc", ""), height=90, label_visibility="collapsed", disabled=disabled_flag, key="s5_desc")
    st.markdown("**이 작품이 지역의 어떤 정체성을 담았는가** *(Step 1 의 근거 자료를 인용하여 쓸 것)*")
    step5_identity = st.text_area("지역 정체성 반영", value=ans.get("step5_identity", ans.get("step5_q1", "")), height=90, label_visibility="collapsed", disabled=disabled_flag, key="s5_identity")
    st.markdown("**현장 조건을 어떻게 작품에 반영했는가** *(Step 3 에서 가장 잘 해결한 조건 1 가지를 골라 쓸 것)*")
    step5_condition = st.text_area("현장 조건 반영", value=ans.get("step5_condition", ans.get("step5_q2", "")), height=90, label_visibility="collapsed", disabled=disabled_flag, key="s5_condition")
    st.markdown("**이 작품이 우리 지역에 남길 변화** *(관람객 / 주민 / 상권 세 측면에서 각각 한 줄씩)*")
    col_c1, col_c2, col_c3 = st.columns(3)
    with col_c1:
        st.markdown("##### 👥 1. 관람객 측면")
        step5_change_visitor = st.text_area("관람객에게 미칠 영향 및 변화", value=ans.get("step5_change_visitor", ""), placeholder="예: 새로운 문화 랜드마크로 인식하고 야간 명소로 방문하게 된다.", height=110, key="step5_change_vis_input", disabled=disabled_flag, label_visibility="collapsed")
    with col_c2:
        st.markdown("##### 🏡 2. 주민 측면")
        step5_change_resident = st.text_area("지역 주민에게 미칠 영향 및 변화", value=ans.get("step5_change_resident", ""), placeholder="예: 어두웠던 공간이 밝아져 자긍심을 느끼고 야간 안심 귀갓길이 형성된다.", height=110, key="step5_change_res_input", disabled=disabled_flag, label_visibility="collapsed")
    with col_c3:
        st.markdown("##### 🛍️ 3. 상권 측면")
        step5_change_market = st.text_area("주변 상권에게 미칠 영향 및 변화", value=ans.get("step5_change_market", ""), placeholder="예: 야간 유동 인구가 늘어 인근 골목 상권이 활성화된다.", height=110, key="step5_change_mkt_input", disabled=disabled_flag, label_visibility="collapsed")

    _mf_gallery(user_key, u_info, current_role, disabled_flag, learning_data)
    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ---------------- Step 6 ----------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 6. 제출 전 자기 점검 및 활용 기록</h3>", unsafe_allow_html=True)
    st.markdown("<br>**▶ 생성형 AI 활용 기록** *(이미지·아이디어 생성에 AI 를 사용한 경우 반드시 기록. 미기재 시 평가에서 제외됩니다.)*", unsafe_allow_html=True)
    ai_cols = ["사용한 도구명", "입력한 프롬프트", "AI 결과물을 내가 수정·판단한 내용"]
    ai_data = ans.get("step6_ai_df") or [{c: "" for c in ai_cols} for _ in range(2)]
    ai_df = pd.DataFrame(ai_data)
    for c in ai_cols:
        if c not in ai_df.columns: ai_df[c] = ""
    ai_df = ai_df[ai_cols].fillna("")
    edited_step6_ai_df = grid_editor(ai_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key="s6_ai_editor",
                                        column_config={"사용한 도구명": st.column_config.TextColumn("사용한 도구명", width="small"),
                                                       "입력한 프롬프트": st.column_config.TextColumn("입력한 프롬프트", width="medium"),
                                                       "AI 결과물을 내가 수정·판단한 내용": st.column_config.TextColumn("내가 수정·판단한 내용", width="medium")})
    st.markdown("<br>**활동 성찰** *(이 활동으로 도시와 나의 진로에 대해 새로 알게 된 점)*", unsafe_allow_html=True)
    step6_reflection = st.text_area("활동 성찰", value=ans.get("step6_reflection", ""), height=100, label_visibility="collapsed", disabled=disabled_flag, key="s6_reflection")
    old_career = any("진로 심화" in str(r.get("점검 항목", "")) and r.get("확인") for r in (ans.get("step6_chk_df") or []))
    career_done = st.checkbox("진로 심화 트랙 산출물을 함께 제출했다. (해당 학생만)", value=bool(old_career), key="s6_career_chk", disabled=disabled_flag)

    new_ans = {
        "ind_id": ind_id, "ind_name": ind_name, "ind_career": ind_career,
        "step1_df": edited_step1_df.to_dict('records'), "step1_keyword": step1_keyword, "step1_message": step1_message,
        "step2_matrix_df": s2_records, "step2_final_building": step2_final_building, "step2_reason": step2_reason,
        "step2_site_photo": site_photo,
        "step3_df": edited_step3_df.to_dict('records') + [r for r in (ans.get("step3_df") or []) if isinstance(r, dict) and any(x in str(r.get("조건 영역", "")) for x in MF_S3_REMOVED)],   # 지운 조건 줄은 숨겨서 보관
        "step4_df": step4_data, "step4_video": saved_video, "step4_video_link": video_link, "step4_facade": facade, "step4_total_sec": total,
        "step5_title": step5_title, "step5_place": step5_place, "step5_desc": step5_desc,
        "step5_identity": step5_identity, "step5_condition": step5_condition,
        "step5_change_visitor": step5_change_visitor, "step5_change_resident": step5_change_resident, "step5_change_market": step5_change_market,
        "step5_change": f"[관람객] {step5_change_visitor} / [주민] {step5_change_resident} / [상권] {step5_change_market}",
        "step6_ai_df": edited_step6_ai_df.to_dict('records'), "step6_reflection": step6_reflection,
    }
    checks = _mf_auto_check(new_ans)
    new_ans["step6_chk_df"] = [{"No": i + 1, "점검 항목": lbl, "확인": bool(ok)} for i, (ok, lbl, _) in enumerate(checks)] + \
                              [{"No": len(checks) + 1, "점검 항목": "진로 심화 트랙 산출물을 함께 제출했다.", "확인": bool(career_done)}]

    st.markdown("**✅ 제출 전 자동 점검** *(작성한 내용을 바탕으로 자동으로 확인합니다)*")
    done = sum(1 for ok, _, _ in checks if ok)
    st.progress(done / len(checks), text=f"완성도 {done}/{len(checks)} 항목")
    lines = []
    for ok, lbl, why in checks:
        lines.append(f"- {'✅' if ok else '⚠️'} **{lbl}**" + ("" if ok or not why else f" — <span style='color:#c53030'>{why}</span>"))
    st.markdown("\n".join(lines), unsafe_allow_html=True)

    autosave_draft(user_key, category, new_ans, disabled_flag)  # 🛟 서버 임시저장
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        commit_activity(user_key, category, new_ans, u_name)


def _mf_gallery(user_key, u_info, current_role, disabled_flag, learning_data):
    """갤러리 워크: 같은 반 친구 작품 감상 + 익명 피드백(선생님이 반별로 공개)"""
    cfg = load_json(CONFIG_FILE, {})
    subj = u_info.get("subject", "전체") if current_role == "학생" else "2학년 도시의 미래 탐구"
    classes = CLASSES_MAP.get("2학년 도시의 미래 탐구", [])
    gal_cfg = cfg.get("gallery_open", {}).get(ACT_2_3, {})
    st.markdown("#### 🖼️ 갤러리 워크 — 친구 작품 감상 및 피드백")
    if current_role == "관리자":
        st.caption("교사용: 반별로 갤러리를 열고 닫을 수 있습니다. (열린 반 학생만 같은 반 작품을 볼 수 있음)")
        gc = st.columns(max(1, len(classes)))
        new_state = {}
        for i, cl in enumerate(classes):
            new_state[cl] = gc[i].toggle(f"{cl} 공개", value=bool(gal_cfg.get(cl, False)), key=f"mf_gal_{cl}")
        if new_state != {c: bool(gal_cfg.get(c, False)) for c in classes}:
            fresh = load_json(CONFIG_FILE, {})
            fresh.setdefault("gallery_open", {})[ACT_2_3] = new_state
            save_json(CONFIG_FILE, fresh)
        view_class = st.selectbox("둘러볼 반", classes, key="mf_gal_view_class") if classes else ""
    else:
        view_class = u_info.get("class_group", "")
        if not gal_cfg.get(view_class, False):
            st.caption("🔒 선생님이 아직 갤러리를 열지 않았습니다.")
            return
    users = load_json(USERS_FILE, {})
    works = []
    for k, acts in learning_data.items():
        a = acts.get(ACT_2_3) or {}
        u = users.get(k, {})
        if k != user_key and u.get("class_group") == view_class and str(a.get("step5_title", "")).strip():
            works.append((k, u, a))
    works.sort(key=lambda x: str(x[1].get("id", "")))
    if not works:
        st.caption("아직 작품 제목까지 작성한 친구가 없습니다.")
    else:
        anon = current_role == "학생"
        label = (lambda i: f"작품 {i + 1}. {works[i][2].get('step5_title')}") if anon else (lambda i: f"{works[i][1].get('name')} ({works[i][1].get('id')}) — {works[i][2].get('step5_title')}")
        pick = st.selectbox(f"감상할 작품 ({len(works)}개)", list(range(len(works))), format_func=label, key="mf_gal_pick")
        tk, tu, ta = works[pick]
        g1, g2 = st.columns([1.2, 1])
        with g1:
            media = _media_ok(ta.get("step4_video")) or next((c.get("스케치_img") for c in ta.get("step4_df", []) if c.get("스케치_img")), "")
            if media: show_media(media, use_container_width=True)
        with g2:
            st.markdown(f"### {ta.get('step5_title')}")
            st.markdown(f"📍 **{ta.get('step5_place', '')}**")
            if ta.get("step1_message"): st.markdown(f"> {ta.get('step1_message')}")
            st.markdown(ta.get("step5_desc", ""))
        if current_role == "학생":
            mine = dict(learning_data.get(user_key, {}).get(MF_GALLERY_ACT, {}) or {})
            prev = mine.get(tk, {})
            with st.form(f"mf_fb_form_{tk}"):
                star = st.select_slider("이 작품은 메시지가 잘 전달되나요?", options=[1, 2, 3, 4, 5], value=int(prev.get("별점", 4)), format_func=lambda v: "★" * v + "☆" * (5 - v))
                good = st.text_area("👍 좋았던 점 (근거를 들어서)", value=prev.get("좋았던 점", ""), height=70)
                idea = st.text_area("💡 더 발전시킬 점 (구체적인 제안)", value=prev.get("제안", ""), height=70)
                if st.form_submit_button("피드백 남기기 (익명)", disabled=disabled_flag):
                    if good.strip() and idea.strip():
                        mine[tk] = {"별점": star, "좋았던 점": good.strip(), "제안": idea.strip(), "시각": get_kst_now().strftime("%Y-%m-%d %H:%M")}
                        try:
                            STORE.set("data", user_key, mine, sub=MF_GALLERY_ACT)
                            st.success("피드백을 남겼습니다. 고마워요!")
                        except Exception as e:
                            st.error(f"저장 실패: {e}")
                    else:
                        st.warning("좋았던 점과 제안을 모두 적어 주세요.")
    # 받은 피드백
    target = user_key if current_role == "학생" else (works[pick][0] if works else None)
    if target:
        recv = [(k, fb[target]) for k, acts in learning_data.items()
                if isinstance((fb := acts.get(MF_GALLERY_ACT)), dict) and target in fb]
        title = "📬 내가 받은 피드백" if current_role == "학생" else "📬 이 작품이 받은 피드백 (교사용: 작성자 표시)"
        with st.expander(f"{title} ({len(recv)}개)", expanded=False):
            if not recv: st.caption("아직 받은 피드백이 없습니다.")
            else:
                st.markdown(f"평균 별점: **{sum(int(f.get('별점', 0)) for _, f in recv) / len(recv):.1f} / 5**")
                for k, f in recv:
                    who = "" if current_role == "학생" else f" — {users.get(k, {}).get('name', '')}"
                    st.markdown(f"- {'★' * int(f.get('별점', 0))} 👍 {f.get('좋았던 점', '')}  \n  💡 {f.get('제안', '')}{who}")


def render_custom_activity(user_key, u_info, current_role, act_name, config):
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, act_name, learning_data)
    ans = apply_draft(user_key, act_name, ans, enabled=(owner_key == user_key))
    is_active, status_msg = check_active_with_exception(act_name, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    if current_role == "관리자": disabled_flag = False; st.info("💡 교사/관리자 모드입니다.")
    
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000;'>♣ {act_name}</h2>", unsafe_allow_html=True)
    if current_role == "학생":
        if disabled_flag: st.error(status_msg.replace('\n', '<br>'), icon="🚫")
        else: st.success(status_msg, icon="✅")
        
    custom_form = config.get("custom_forms", {}).get(act_name, [])
    if not custom_form: st.info("등록된 질문(문항)이 없습니다.")
    
    new_ans = {}
    for q in custom_form:
        q_id, q_label, q_type = q["id"], q["label"], q["type"]
        st.markdown(f"**{q_label}**")
        if q_type == "text": 
            new_ans[q_id] = st.text_input(f"{q_label} 입력", value=ans.get(q_id, ""), disabled=disabled_flag, label_visibility="collapsed", key=f"{q_id}_{act_name}")
        elif q_type == "textarea": 
            new_ans[q_id] = st.text_area(f"{q_label} 입력", value=ans.get(q_id, ""), height=150, disabled=disabled_flag, label_visibility="collapsed", key=f"{q_id}_{act_name}")
            
    if custom_form: autosave_draft(user_key, act_name, new_ans, disabled_flag)  # 🛟 서버 임시저장
    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{act_name}"):
            # (버그 수정) 정의되지 않은 step4_data 참조로 저장할 때마다 오류가 나던 줄 제거
            if commit_activity(user_key, act_name, new_ans, u_name, "🎉 화면 저장이 완료되었습니다!"):
                ans = new_ans
            
    if ans:
        html_data = f"<!DOCTYPE html><html lang='ko'><head><meta charset='UTF-8'><title>{u_name}</title></head><body><h2>▶ {act_name}</h2>"
        for q in custom_form: html_data += f"<h3>{q['label']}</h3><p>{ans.get(q['id'],'')}</p>"
        html_data += "</body></html>"
        lazy_download("내 작성 내용 다운로드 (웹문서)", f"custom::{user_key}::{act_name}", lambda: html_data, f"{u_name}_{act_name}.html", "text/html")

def lazy_download(label, key, build_fn, file_name, mime="application/octet-stream", **kw):
    """내려받기 파일은 [만들기]를 눌렀을 때만 만듦.
    (예전에는 화면을 그릴 때마다 전체 자료·전체 포트폴리오 파일을 미리 만들어 서버 메모리를 수백 MB씩 썼음)"""
    bk = f"_dl_bytes::{key}"
    if st.session_state.get(bk) is None:
        if st.button(f"⚙️ {label} — 파일 만들기", key=f"mk::{key}", **kw):
            with st.spinner("파일을 만드는 중입니다..."):
                data = build_fn()
            st.session_state[bk] = data.encode("utf-8-sig") if isinstance(data, str) else data
    if st.session_state.get(bk) is not None:
        c1, c2 = st.columns([4, 1])
        with c1:
            st.download_button(f"📥 {label}", data=st.session_state[bk], file_name=file_name, mime=mime, key=f"dl::{key}", **kw)
        with c2:
            if st.button("닫기", key=f"cl::{key}", help="만든 파일을 서버 메모리에서 지웁니다"):
                st.session_state.pop(bk, None)
                st.rerun()

def render_class_overview(current_role, u_info, view_subj):
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; margin-bottom: 20px;'>🎯 [{view_subj}] 수행평가 및 활동 모듈</h2>", unsafe_allow_html=True)
    st.markdown("---")
    app_config = load_json(CONFIG_FILE, {})
    user_class_group = u_info.get('class_group', '')
    
    custom_blocks = [b for b in app_config.get("custom_blocks", []) if b.get("subject", "전체 공지") in ["전체 공지", view_subj]]
    for block in custom_blocks:
        st.markdown(f"""<div style="border: 2px solid #4CAF50; border-radius: 8px; margin-bottom: 20px;"><div style="background-color: #4CAF50; color: white; padding: 10px 20px; font-size: 22px; font-weight: 900;">{block["title"]}</div><div style="padding: 15px; font-size: 18px;">{block["content"].replace(chr(10), '<br>')}</div></div>""", unsafe_allow_html=True)

    dynamic_links = [l for l in app_config.get("dynamic_links", []) if l.get("subject", "전체 공지") in ["전체 공지", view_subj]]
    if dynamic_links:
        grouped_links = {}
        for link in dynamic_links: grouped_links.setdefault(link['group'], []).append(link)
        link_cols = st.columns(2)
        col_idx = 0
        for group_name, links in grouped_links.items():
            with link_cols[col_idx % 2]:
                with st.expander(group_name, expanded=True):
                    for link in links: st.markdown(f"**[{link['title']}]({link['url']})**")
            col_idx += 1
        st.markdown("---")

    all_notices = app_config.get("notices", [])
    filtered_notices = []
    for notice in all_notices:
        n_subj = notice.get("subject", "전체 공지")
        n_class = notice.get("target_class", "전체")
        subj_match = (n_subj == "전체 공지" or n_subj == view_subj)
        if current_role == "학생": class_match = (n_class in ["전체", "전체 반", "전체 공지"] or n_class == user_class_group)
        else: class_match = True
        if subj_match and class_match: filtered_notices.append(notice)

    if filtered_notices:
        st.markdown("<h3 style='font-size: 24px; font-weight: 800;'>📢 알림 및 공지사항</h3>", unsafe_allow_html=True)
        for notice in filtered_notices:
            t, c, target_c = notice.get("제목", "").strip(), notice.get("내용", "").strip(), notice.get("target_class", "전체")
            badge = f" [{target_c}]" if target_c not in ["전체", "전체 반"] else ""
            if t or c: st.info(f"**{t}**{badge}\n\n{c}")
        st.markdown("---")

    materials = app_config.get("materials", [])
    if materials:
        st.markdown("<h3 style='font-size: 24px; font-weight: 800;'>👨‍🏫 수업 공지 및 자료실</h3>", unsafe_allow_html=True)
        for mat in materials:
            if mat.get("subject", "전체 공지") in ["전체 공지", view_subj]:
                if mat["type"] == "link": st.markdown(f"🔗 **[{mat['title']}]({mat['content']})**")
                elif mat["type"] == "file" and os.path.exists(mat["content"]):
                    with open(mat["content"], "rb") as f: st.download_button(f"📥 {mat['title']} 다운로드", f, file_name=mat['filename'], key=f"mat_dl_{mat['id']}")
        st.markdown("---")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800;'>📝 학년별 수행평가 목록</h3>", unsafe_allow_html=True)
    acts_for_subj = app_config.get("subject_activities", {}).get(view_subj, [])

    if current_role == "학생": display_acts = [a for a in acts_for_subj if is_act_visible_for_user(a, user_class_group, current_user_key, app_config)]
    else: display_acts = acts_for_subj

    if display_acts:
        cols = st.columns(3)
        for idx, act in enumerate(display_acts):
            with cols[idx % 3]:
                prefix = "🔒 (비공개) " if current_role == "관리자" and not is_act_visible_for_class(act, user_class_group, app_config) else "📄 "
                if st.button(f"{prefix}{act}", use_container_width=True, key=f"btn_go_{act}"): change_page(act)
    else:
        if current_role == "학생": st.info("현재 공개되어 진행 중인 수행평가가 없습니다. (선생님의 수업 시간 공개 설정을 기다려주세요.)")
        else: st.info("아직 이 과목에 할당된 수행평가 목록이 없습니다.")

# 🌟 [해결됨] 누락되었던 세션 초기화 블록 완벽 복원
if "logged_in" not in st.session_state: 
    st.session_state.logged_in = False
    st.session_state.user_info = None

if "session_token" in st.query_params and not st.session_state.logged_in:
    token = st.query_params["session_token"]
    user_key = decode_token(token)
    if user_key:
        users = load_json(USERS_FILE, {})
        if user_key in ADMIN_ACCOUNTS:
            st.session_state.logged_in = True
            st.session_state.user_info = {"user_key": user_key, "id": user_key, "name": ADMIN_ACCOUNTS[user_key]["name"], "role": "관리자", "subject": "전체", "class_group": "관리자"}
        elif user_key in users and users[user_key].get("approved", True):
            st.session_state.logged_in = True
            st.session_state.user_info = users[user_key]
            st.session_state.user_info["user_key"] = user_key

if st.session_state.logged_in and st.session_state.user_info:
    st.query_params["session_token"] = encode_token(st.session_state.user_info["user_key"])

if "current_page" in st.query_params: st.session_state.current_page = st.query_params["current_page"]
elif "current_page" not in st.session_state: st.session_state.current_page = "main"

# =====================================================================
# 🚀 메인 실행부 및 사이드바 (UI 구성)
# =====================================================================

st.session_state["_wkey_suffix"] = None       # 학생 화면·관리 화면에서는 입력칸 이름을 바꾸지 않음
# 다른 화면(메인·다른 활동지)으로 이동했을 때만 활동지 고정 내용을 새로 불러옴
if st.session_state.get("_last_page_seen") != st.session_state.current_page:
    for _fk in [k for k in list(st.session_state.keys()) if str(k).startswith(("_frozen_ans::", "_dl_bytes::"))]:
        del st.session_state[_fk]
    st.session_state["_last_page_seen"] = st.session_state.current_page
st.session_state["_render_prev"] = st.session_state.get("_render_now")
st.session_state["_render_now"] = None

if not STORE.sheets_ready:
    # 서버가 막 재시작되어 구글 시트에서 자료를 받아오는 중 → 예전 자료 위에 저장하는 사고를 막기 위해 잠시 대기
    st.markdown("<h1 style='font-size:32px; font-weight:900;'>🏫 수업 및 활동 어시스트 프로그램</h1>", unsafe_allow_html=True)
    st.warning("⏳ 저장소(구글 시트)와 자료를 동기화하는 중입니다. 10초 정도 뒤에 새로고침(F5) 해 주세요.")
    st.stop()

init_system()

st.sidebar.title("🔒 인증 센터")

if st.session_state.logged_in:
    u_info = st.session_state.user_info
    if u_info['role'] == "관리자": 
        st.sidebar.markdown(f"<div style='background-color:#e8f4f8; padding:10px; border-radius:5px; margin-bottom:15px;'><div style='font-size:16px; font-weight:bold; color:#0056b3;'>🟢 {u_info['name']} 님 로그인 중</div><div>📘 과목: {u_info.get('subject', '전체')}</div><div>🛡️ 권한: {u_info['role']}</div></div>", unsafe_allow_html=True)
        st.session_state.admin_view_subject = st.sidebar.selectbox("👀 관리 및 미리보기 과목", ["전체 공지"] + SUBJECTS)
    else: 
        st.sidebar.markdown(f"<div style='background-color:#e8f4f8; padding:10px; border-radius:5px; margin-bottom:15px;'><div style='font-size:16px; font-weight:bold; color:#0056b3;'>🟢 {u_info['name']} 님 로그인 중</div><div>📘 과목: {u_info.get('subject', '전체')}</div><div>🏫 소속: {u_info.get('class_group', '')}</div><div>🛡️ 권한: {u_info['role']}</div></div>", unsafe_allow_html=True)
        
    if st.sidebar.button("로그아웃", type="primary", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.user_info = None
        st.session_state.current_page = "main"
        st.query_params.clear()
        st.rerun()
else:
    auth_choice = st.sidebar.radio("원하는 작업을 선택하세요", ["회원가입", "로그인"])
    users = load_json(USERS_FILE, {})
    if auth_choice == "회원가입":
        st.sidebar.subheader("📝 회원가입")
        reg_subject = st.sidebar.selectbox("과목", SUBJECTS)
        reg_class = st.sidebar.selectbox("반", CLASSES_MAP[reg_subject])
        with st.sidebar.form("register_form"):
            reg_id = st.text_input("학번 입력")
            reg_name = st.text_input("이름 입력")
            reg_pw = st.text_input("비밀번호", type="password")
            if st.form_submit_button("가입 신청", type="primary", use_container_width=True):
                if reg_subject and reg_class and reg_id and reg_name and reg_pw:
                    user_key = f"{reg_subject.strip()}_{reg_class.strip()}_{reg_id.strip()}"
                    fresh_users = load_json(USERS_FILE, {}) 
                    if user_key in fresh_users: st.error("❌ 해당 학번이 이미 가입되어 있습니다.")
                    else:
                        fresh_users[user_key] = {"id": reg_id.strip(), "password": reg_pw.strip(), "name": reg_name.strip(), "role": "학생", "subject": reg_subject.strip(), "class_group": reg_class.strip(), "approved": False}
                        save_json(USERS_FILE, fresh_users)
                        create_auto_backup(f"[{reg_name}] 회원가입 신청")
                        st.success("🎉 가입 완료! 선생님의 승인을 기다려주세요.")
                else: st.warning("⚠️ 모든 빈칸을 빠짐없이 입력해주세요.")
                
    elif auth_choice == "로그인":
        login_type = st.sidebar.radio("계정 유형", ["학생", "교사(관리자)"], horizontal=True)
        if login_type == "학생":
            login_subject = st.sidebar.selectbox("과목", SUBJECTS)
            login_class = st.sidebar.selectbox("반", CLASSES_MAP[login_subject])
            with st.sidebar.form("student_login_form"):
                input_id = st.text_input("학번")
                input_pw = st.text_input("비밀번호", type="password")
                if st.form_submit_button("로그인", type="primary", use_container_width=True):
                    user_key = f"{login_subject.strip()}_{login_class.strip()}_{input_id.strip()}"
                    if user_key in users and users[user_key].get("password") == input_pw.strip():
                        if users[user_key].get("approved", True):
                            st.session_state.logged_in = True
                            st.session_state.user_info = users[user_key]
                            st.session_state.user_info["user_key"] = user_key
                            st.query_params["session_token"] = encode_token(user_key)
                            st.rerun()
                        else: st.warning("⏳ 선생님의 가입 승인을 대기 중입니다.")
                    else: st.error("❌ 과목, 반, 학번 또는 비밀번호가 틀렸습니다.")
        else:
            with st.sidebar.form("admin_login_form"):
                input_id = st.text_input("관리자 ID")
                input_pw = st.text_input("비밀번호", type="password")
                if st.form_submit_button("로그인", type="primary", use_container_width=True):
                    if input_id in ADMIN_ACCOUNTS and input_pw == ADMIN_ACCOUNTS[input_id]["pw"]:
                        st.session_state.logged_in = True
                        st.session_state.user_info = {"user_key": input_id, "id": input_id, "name": ADMIN_ACCOUNTS[input_id]["name"], "role": "관리자", "subject": "전체", "class_group": "관리자"}
                        st.query_params["session_token"] = encode_token(input_id)
                        st.rerun()
                    else: st.error("❌ 관리자 정보가 틀렸습니다.")

st.sidebar.markdown("---")
st.sidebar.markdown("<div style='text-align: center; color: #222; font-size: 18px; font-weight: 900;'>Made by<br><span style='font-size: 24px; color: #000;'>신선여자고등학교 김명남</span></div><br>", unsafe_allow_html=True)

with st.sidebar: render_privacy_policy()

# 🛟 브라우저 임시저장 스크립트(로그인한 학생의 활동지 화면에서만, 학생·화면별로 분리)
if st.session_state.get("logged_in") and st.session_state.get("user_info"):
    inject_custom_scripts(f"{st.session_state.user_info.get('user_key','')}|{st.session_state.get('current_page','main')}",
                          clear=st.session_state.pop("_autosave_clear", False))

if not st.session_state.logged_in:
    st.markdown("<h1 style='font-size:36px; font-weight:900; color:#000;'>🏫 수업 및 활동 어시스트 프로그램</h1>", unsafe_allow_html=True)
    st.info("왼쪽 사이드바를 이용해 로그인해주세요.")
    st.markdown("---")
    render_privacy_policy()
else:
    current_role = st.session_state.user_info["role"]
    current_user_key = st.session_state.user_info["user_key"]
    u_info = st.session_state.user_info
    user_class_group = u_info.get('class_group', '')
    app_config = load_json(CONFIG_FILE, {})
    learning_data = load_json(DATA_FILE, {})

    if st.session_state.current_page != "main":
        act_name = st.session_state.current_page
        is_user_exc = is_act_visible_for_user(act_name, user_class_group, current_user_key, app_config)
        
        if current_role == "학생" and not is_user_exc:
            st.error(f"🚫 현재 [{user_class_group}]은(는) 이 수행평가가 비공개 상태입니다.")
            if st.button("⬅️ 메인 화면으로 돌아가기", use_container_width=True): change_page("main")
        else:
            lock_table_headers()   # 표 제목줄 잘못 터치 → 행·열 순서 바뀜 방지
            if act_name == ACT_3_1: render_activity1_3th(current_user_key, u_info, current_role)
            elif act_name == ACT_3_2: render_activity2_3th(current_user_key, u_info, current_role)
            elif act_name == ACT_3_3: render_activity3_3th(current_user_key, u_info, current_role)
            elif act_name == ACT_2_1: render_activity1_2nd(current_user_key, u_info, current_role)
            elif act_name == ACT_2_2: render_activity2_2nd(current_user_key, u_info, current_role)
            elif act_name == ACT_2_3: render_activity3_2nd(current_user_key, u_info, current_role)
            else: render_custom_activity(current_user_key, u_info, current_role, act_name, app_config)
            st.markdown("<br><br>", unsafe_allow_html=True)
            if st.button("⬅️ 메인 화면으로 돌아가기", use_container_width=True): change_page("main")
    else:
        if current_role == "학생":
            st.markdown("<h1 style='font-size:36px; font-weight:900; color:#000;'>🏫 수업 및 활동 어시스트 프로그램</h1>", unsafe_allow_html=True)
            render_class_overview(current_role, u_info, u_info.get('subject', '전체'))
            st.markdown("---")
            acts_for_subj = app_config.get("subject_activities", {}).get(u_info['subject'], [])
            has_individual_dl = False
            for act in acts_for_subj:
                owner_key, ans = get_user_activity_data(current_user_key, u_info.get('id', ''), u_info.get('subject', '전체'), u_info.get('class_group', ''), act, learning_data)
                if ans:
                    has_individual_dl = True
                    lazy_download(f"[{act}] 결과물 다운로드", f"stu::{current_user_key}::{act}",
                                  (lambda a=act, x=ans: generate_activity_html(a, x, u_info['name'])), f"{u_info['name']}_{act}.html", "text/html")
            if not has_individual_dl: st.info("아직 제출한 활동지가 없습니다.")
            st.markdown("---")
            lazy_download(f"{u_info['name']} 학생 전체 포트폴리오 일괄 다운로드 (웹문서)", f"stu_all::{current_user_key}",
                          lambda: generate_portfolio_html(current_user_key, u_info, u_info['subject'], app_config, learning_data),
                          f"{u_info['name']}_전체_포트폴리오.html", "text/html", type="primary")

        elif current_role == "관리자":
            st.markdown("<h1 style='font-size:36px; font-weight:900; color:#000;'>🛠️ 관리자(교사) 대시보드</h1>", unsafe_allow_html=True)
            menu_tabs = st.tabs(["📌 메인 화면/기한 설정", "🗂️ 수행평가 문항 제작", "👥 회원 관리", "📥 학생 제출 자료 조회 및 관리", "💾 DB 수동 백업 및 복구", "🛡️ 자동 백업 센터"])
            
            with menu_tabs[0]:
                if st.session_state.get("admin_save_success", False):
                    st.balloons(); st.success("🎉 저장/반영이 완료되었습니다!")
                    st.session_state.admin_save_success = False

                admin_view_subj = st.session_state.get("admin_view_subject", "전체 공지")
                st.markdown(f"### 🖥️ [{admin_view_subj}] 학생 화면 미리보기 및 활동지 테스트")
                render_class_overview(current_role, u_info, admin_view_subj)
                st.markdown("---")
                
                st.markdown(f"### ⚙️ [{admin_view_subj}] 메인 화면 편집 및 기한/반별 공개/공지 설정")
                fresh_config = load_json(CONFIG_FILE, {})
                
                st.markdown("#### 🔒 학급(반)별 수행평가 공개 및 비공개 설정")
                if admin_view_subj in SUBJECTS:
                    acts_for_vis = fresh_config.get("subject_activities", {}).get(admin_view_subj, [])
                    classes_for_vis = CLASSES_MAP.get(admin_view_subj, [])
                    if acts_for_vis:
                        vis_map = fresh_config.get("activity_visibility", {})
                        with st.form(f"vis_form_class_{admin_view_subj}"):
                            updated_vis_by_class = {}
                            for act in acts_for_vis:
                                st.markdown(f"**📄 {act}**")
                                act_vis_dict = vis_map.get(act, {})
                                if not isinstance(act_vis_dict, dict): act_vis_dict = {}
                                cols_cls = st.columns(len(classes_for_vis))
                                updated_vis_by_class[act] = {}
                                for i, c_group in enumerate(classes_for_vis):
                                    with cols_cls[i]:
                                        cur_cls_val = act_vis_dict.get(c_group, True)
                                        updated_vis_by_class[act][c_group] = st.toggle(f"🏫 {c_group}", value=cur_cls_val, key=f"toggle_vis_{admin_view_subj}_{act}_{c_group}")
                                st.markdown("<hr style='margin: 10px 0; border: 0; border-top: 1px dashed #ddd;'>", unsafe_allow_html=True)
                            if st.form_submit_button(f"[{admin_view_subj}] 반별 공개/비공개 설정 일괄 저장", type="primary"):
                                if "activity_visibility" not in fresh_config: fresh_config["activity_visibility"] = {}
                                for act, cls_dict in updated_vis_by_class.items(): fresh_config["activity_visibility"][act] = cls_dict
                                save_json(CONFIG_FILE, fresh_config)
                                create_auto_backup(f"[{admin_view_subj}] 반별 공개 설정 변경")
                                st.session_state.admin_save_success = True; st.rerun()
                else: st.info("🔒 반별 공개 설정은 왼쪽 사이드바에서 특정 과목을 선택해주세요.")

                st.markdown("---")
                st.markdown("#### ⏳ 특정 학생 개별 기한 연장 (결석생/추가 제출자)")
                st.info("학급 전체 비공개/마감 상태여도, 지정된 학생은 설정된 시간까지 해당 수행평가를 작성하고 제출할 수 있습니다.")
                if admin_view_subj in SUBJECTS:
                    acts_for_subj = fresh_config.get("subject_activities", {}).get(admin_view_subj, [])
                    if acts_for_subj:
                        col_ex1, col_ex2 = st.columns([1, 1])
                        with col_ex1:
                            exc_act = st.selectbox("기한을 연장할 수행평가", acts_for_subj, key="exc_act")
                            all_users = load_json(USERS_FILE, {})
                            stu_in_subj = {k: v for k, v in all_users.items() if v.get("role") == "학생" and v.get("subject") == admin_view_subj}
                            search_exc = st.text_input("🔍 연장할 학생 검색 (이름/학번)", key="search_exc")
                            filt_stu = {k: v for k, v in stu_in_subj.items() if v.get("approved", True) and search_exc.lower() in f"{v.get('class_group','')} {v.get('name','')} {v.get('id','')}".lower()}
                            _fmt_stu = lambda x: "선택" if x == "선택" else f"[{filt_stu[x].get('class_group')}] {filt_stu[x].get('name')} ({filt_stu[x].get('id')})"
                            _stu_keys = sorted(filt_stu.keys(), key=lambda x: (str(filt_stu[x].get('class_group', '')), str(filt_stu[x].get('id', ''))))
                            if search_exc.strip() and not _stu_keys:
                                st.warning("검색된 학생이 없습니다. 이름이나 학번을 다시 확인해 주세요.")
                                sel_stu = "선택"
                            elif search_exc.strip() and len(_stu_keys) <= 10:
                                # 🔍 검색하면 결과가 바로 보이고 첫 번째 학생이 자동 선택됨
                                sel_stu = st.radio(f"학생 선택 (검색 결과 {len(_stu_keys)}명)", _stu_keys, format_func=_fmt_stu, key=f"sel_stu_r_{search_exc.strip()}")
                            else:
                                if search_exc.strip(): st.caption(f"검색 결과가 {len(_stu_keys)}명입니다. 이름이나 학번을 더 입력하면 바로 선택할 수 있습니다.")
                                sel_stu = st.selectbox("학생 선택", ["선택"] + _stu_keys, format_func=_fmt_stu, key="sel_stu_tab0")
                            col_ed1, col_ed2 = st.columns(2)
                            exc_date = col_ed1.date_input("연장 마감일", value=get_kst_now().date() + datetime.timedelta(days=1), key="exc_date")
                            exc_time = col_ed2.selectbox("연장 마감 시간", TIME_OPTIONS, index=get_time_index("23:50"), key="exc_time")
                            if st.button("개별 연장 기한 부여", type="primary"):
                                if sel_stu != "선택":
                                    if "exceptions" not in fresh_config: fresh_config["exceptions"] = {}
                                    if exc_act not in fresh_config["exceptions"]: fresh_config["exceptions"][exc_act] = {}
                                    fresh_config["exceptions"][exc_act][sel_stu] = f"{exc_date} {exc_time}"
                                    save_json(CONFIG_FILE, fresh_config); create_auto_backup(f"[{filt_stu[sel_stu].get('name')}] 예외 연장"); st.session_state.admin_save_success = True; st.rerun()
                                else: st.warning("학생을 선택해주세요.")
                        with col_ex2:
                            st.markdown("**부여된 연장 기한 목록**")
                            cur_exc = fresh_config.get("exceptions", {}).get(exc_act, {})
                            if cur_exc:
                                for e_uid, e_dl in list(cur_exc.items()):
                                    e_info = all_users.get(e_uid, {})
                                    e_label = f"[{e_info.get('class_group', '')}] {e_info.get('name', '알수없음')} ({e_info.get('id', '')})"
                                    st.markdown(f"- **{e_label}** : ~ {e_dl} 까지")
                                    if st.button(f"❌ {e_info.get('name', '')} 연장 취소", key=f"del_exc_{e_uid}"):
                                        del fresh_config["exceptions"][exc_act][e_uid]
                                        save_json(CONFIG_FILE, fresh_config); st.rerun()
                            else: st.caption("현재 이 수행평가에 개별 권한이 연장된 학생이 없습니다.")

                st.markdown("---")
                st.markdown("#### 📢 메인 화면 맞춤형 공지사항 관리 (학년/과목/학반별 세분화)")
                all_notices = fresh_config.get("notices", [])
                current_notices = [n for n in all_notices if n.get("subject", "전체 공지") == admin_view_subj]
                target_class_options = ["전체"] if admin_view_subj == "전체 공지" else ["전체 반"] + CLASSES_MAP.get(admin_view_subj, [])
                df_notices_raw = [{"대상 학급(반)": n.get("target_class", target_class_options[0]), "제목": n.get("제목", ""), "내용": n.get("내용", "")} for n in current_notices]
                df_notices = pd.DataFrame(df_notices_raw) if df_notices_raw else pd.DataFrame([{"대상 학급(반)": target_class_options[0], "제목": "", "내용": ""}])
                edited_notices = st.data_editor(df_notices, column_config={"대상 학급(반)": st.column_config.SelectboxColumn("대상 학급(반)", options=target_class_options, required=True, width="medium"), "제목": st.column_config.TextColumn("제목", width="large", required=True), "내용": st.column_config.TextColumn("내용", width="large", required=True)}, num_rows="dynamic", use_container_width=True, hide_index=True, key=f"notice_editor_{admin_view_subj}")
                if st.button(f"📢 [{admin_view_subj}] 맞춤형 공지사항 저장 및 즉시 반영", type="primary"):
                    new_notices = []
                    for row in edited_notices.to_dict('records'):
                        t_cls = str(row.get("대상 학급(반)", target_class_options[0])).strip()
                        t_title = str(row.get("제목", "")).strip()
                        t_content = str(row.get("내용", "")).strip()
                        if t_title or t_content: new_notices.append({"id": f"not_{datetime.datetime.now().strftime('%d%H%M%S')}_{len(new_notices)}", "subject": admin_view_subj, "target_class": t_cls, "제목": t_title, "내용": t_content})
                    other_notices = [n for n in all_notices if n.get("subject", "전체 공지") != admin_view_subj]
                    fresh_config["notices"] = other_notices + new_notices
                    save_json(CONFIG_FILE, fresh_config)
                    create_auto_backup(f"[{admin_view_subj}] 공지사항 업데이트")
                    st.session_state.admin_save_success = True; st.rerun()

                st.markdown("---")
                st.markdown("#### 📝 자유 텍스트/공지 블록 추가 (메인 화면)")
                col_cb1, col_cb2 = st.columns(2)
                with col_cb1:
                    with st.form("add_custom_block"):
                        cb_title = st.text_input("블록 제목")
                        cb_content = st.text_area("내용 입력")
                        if st.form_submit_button("블록 생성하기", type="primary"):
                            if cb_title and cb_content:
                                new_block = {"id": f"cb_{datetime.datetime.now().strftime('%d%H%M%S')}", "title": cb_title, "content": cb_content, "subject": admin_view_subj}
                                fresh_config.setdefault("custom_blocks", []).append(new_block)
                                save_json(CONFIG_FILE, fresh_config)
                                create_auto_backup(f"[{admin_view_subj}] 블록 추가")
                                st.session_state.admin_save_success = True; st.rerun()
                with col_cb2:
                    current_blocks = [b for b in fresh_config.get("custom_blocks", []) if b.get("subject", "전체 공지") == admin_view_subj]
                    if current_blocks:
                        del_cb_target = st.selectbox("삭제할 블록", current_blocks, format_func=lambda x: x["title"])
                        if st.button("블록 삭제하기", type="primary"):
                            fresh_config["custom_blocks"] = [b for b in fresh_config.get("custom_blocks", []) if b["id"] != del_cb_target["id"]]
                            save_json(CONFIG_FILE, fresh_config)
                            create_auto_backup(f"[{admin_view_subj}] 블록 삭제")
                            st.session_state.admin_save_success = True; st.rerun()

                st.markdown("---")
                st.markdown("#### ⏰ 과목/반별 수행평가 수업 시간표 및 제출 기한 설정")
                if admin_view_subj in SUBJECTS:
                    acts_for_subj = fresh_config.get("subject_activities", {}).get(admin_view_subj, [])
                    if acts_for_subj:
                        selected_act_for_setting = st.selectbox("시간표를 설정할 수행평가 선택", acts_for_subj)
                        time_input_mode = st.radio("⏰ 시간 입력 방식", ["🔘 드롭다운 선택 (10분 단위)", "🔘 직접 타이핑 (자유 입력)"], horizontal=True)
                        
                        # 🌟 [추가된 핵심 기능] 시간표 개수를 자유롭게 조절하는 버튼 (폼 외부에 있어 즉시 반응함)
                        slot_count = st.number_input("➕ 각 반별 입력할 수업 시간표 개수 (원하는 만큼 늘리거나 줄이세요)", min_value=1, max_value=20, value=3)
                        
                        new_act_deadlines = fresh_config.setdefault("deadlines", {}).get(selected_act_for_setting, {})
                        with st.form(f"deadline_form_for_{selected_act_for_setting}"):
                            for c_group in CLASSES_MAP[admin_view_subj]:
                                with st.expander(f"🏫 {c_group} 시간표 설정", expanded=False):
                                    c_data = new_act_deadlines.get(c_group, {})
                                    c_final = c_data.get("final_dl", "2030-12-31 23:59")
                                    try: cf_dt = datetime.datetime.strptime(c_final, "%Y-%m-%d %H:%M")
                                    except: cf_dt = get_kst_now() + datetime.timedelta(days=30)
                                    col_f1, col_f2 = st.columns(2)
                                    f_date = col_f1.date_input(f"[{c_group}] 최종 마감일", value=cf_dt.date(), key=f"f_date_{c_group}")
                                    if time_input_mode == "🔘 드롭다운 선택 (10분 단위)":
                                        f_time_str = col_f2.selectbox(f"[{c_group}] 최종 마감 시간", TIME_OPTIONS, index=get_time_index(cf_dt.strftime("%H:%M")), key=f"f_time_sel_{c_group}")
                                    else:
                                        f_time_str = col_f2.text_input(f"[{c_group}] 최종 마감 시간", value=cf_dt.strftime("%H:%M"), key=f"f_time_txt_{c_group}")
                                    
                                    # 🌟 [수정됨] 하드코딩된 3을 조절 가능한 slot_count로 변경
                                    c_slots = c_data.get("slots", [{"day": "선택안함", "period": "선택안함", "start": "00:00", "end": "00:00"}] * slot_count)
                                    while len(c_slots) < slot_count: c_slots.append({"day": "선택안함", "period": "선택안함", "start": "00:00", "end": "00:00"})
                                    
                                    updated_slots = []
                                    # 🌟 [수정됨] 3 대신 사용자가 선택한 개수(slot_count)만큼 칸 생성
                                    for i in range(slot_count):
                                        sc1, sc2, sc3, sc4 = st.columns(4)
                                        day_opts = ["선택안함", "월", "화", "수", "목", "금"]
                                        period_opts = ["선택안함", "1교시", "2교시", "3교시", "4교시", "5교시", "6교시", "7교시", "8교시", "방과후"]
                                        cur_day, cur_period = c_slots[i].get("day", "선택안함"), c_slots[i].get("period", "선택안함")
                                        slot_day = sc1.selectbox(f"수업 {i+1} 요일", day_opts, index=day_opts.index(cur_day) if cur_day in day_opts else 0, key=f"day_{c_group}_{i}")
                                        slot_period = sc2.selectbox(f"수업 {i+1} 교시", period_opts, index=period_opts.index(cur_period) if cur_period in period_opts else 0, key=f"period_{c_group}_{i}")
                                        slot_start_str = sc3.selectbox(f"수업 {i+1} 시작", TIME_OPTIONS, index=get_time_index(c_slots[i].get("start", "09:00")), key=f"st_sel_{c_group}_{i}") if time_input_mode.startswith("🔘 드롭") else sc3.text_input(f"수업 {i+1} 시작", value=c_slots[i].get("start", "09:00"), key=f"st_txt_{c_group}_{i}")
                                        slot_end_str = sc4.selectbox(f"수업 {i+1} 종료", TIME_OPTIONS, index=get_time_index(c_slots[i].get("end", "09:50")), key=f"en_sel_{c_group}_{i}") if time_input_mode.startswith("🔘 드롭") else sc4.text_input(f"수업 {i+1} 종료", value=c_slots[i].get("end", "09:50"), key=f"en_txt_{c_group}_{i}")
                                        updated_slots.append({"day": slot_day, "period": slot_period, "start": slot_start_str, "end": slot_end_str})
                                    new_act_deadlines[c_group] = {"final_dl": f"{f_date} {f_time_str}", "slots": updated_slots}
                            if st.form_submit_button("시간표 및 마감일 저장", type="primary"):
                                fresh_config["deadlines"][selected_act_for_setting] = new_act_deadlines
                                save_json(CONFIG_FILE, fresh_config)
                                create_auto_backup(f"[{selected_act_for_setting}] 시간표 변경")
                                st.session_state.admin_save_success = True; st.rerun()
            with menu_tabs[1]:
                st.markdown("### 🗂️ 과목별 수행평가 목록 관리")
                fresh_config = load_json(CONFIG_FILE, {})
                edit_subj = st.selectbox("과목 선택", SUBJECTS, key="edit_subj")
                acts_list = fresh_config.get("subject_activities", {}).get(edit_subj, [])
                col_add, col_del = st.columns(2)
                with col_add:
                    new_act_name = st.text_input("➕ 새 수행평가 제목")
                    if st.button("추가하기", type="primary"):
                        if new_act_name and new_act_name not in acts_list:
                            fresh_config["subject_activities"][edit_subj].append(new_act_name)
                            fresh_config.setdefault("custom_forms", {})[new_act_name] = [{"id": "q_1", "type": "textarea", "label": "수행평가 내용"}]
                            save_json(CONFIG_FILE, fresh_config)
                            create_auto_backup(f"[{edit_subj}] 새 수행평가 추가: {new_act_name}")
                            st.success("추가 완료"); st.rerun()
                with col_del:
                    if acts_list:
                        del_act_name = st.selectbox("❌ 삭제할 수행평가", ["선택"] + acts_list)
                        if del_act_name != "선택" and st.button("영구 삭제", type="primary"):
                            fresh_config["subject_activities"][edit_subj].remove(del_act_name)
                            save_json(CONFIG_FILE, fresh_config)
                            create_auto_backup(f"[{edit_subj}] 수행평가 삭제: {del_act_name}")
                            st.success("삭제 완료"); st.rerun()

            with menu_tabs[2]:
                def filter_students_admin(user_dict, search_term, approved_only=True):
                    filtered = {}
                    for k, v in user_dict.items():
                        if v.get("role") != "학생": continue
                        if approved_only and not v.get("approved", True): continue
                        if search_term.lower() in f"{v.get('subject','')} {v.get('class_group','')} {v.get('name','')} {v.get('id','')}".lower(): 
                            filtered[k] = v
                    return filtered

                all_users = load_json(USERS_FILE, {})
                pending_users = {k: v for k, v in all_users.items() if not v.get("approved", True) and v.get("role")=="학생"}
                
                st.markdown("### ⏳ 가입 승인 대기 목록")
                if pending_users:
                    df_pending = pd.DataFrame([{"과목": v.get("subject", "-"), "반": v.get("class_group", "-"), "학번": v.get("id", "-"), "이름": v.get("name", "-")} for k, v in pending_users.items()])
                    st.dataframe(df_pending, use_container_width=True)
                    pending_classes = sorted(list(set([v.get("class_group", "") for v in pending_users.values() if v.get("class_group")])))
                    col_p1, col_p2, col_p3 = st.columns([1.5, 1.5, 1.5])
                    with col_p1:
                        approve_class_sel = st.selectbox("🏫 반별 선택", ["선택"] + pending_classes)
                        if approve_class_sel != "선택" and st.button(f"✅ [{approve_class_sel}] 일괄 승인", type="primary", use_container_width=True):
                            fresh_users = load_json(USERS_FILE, {})
                            count = 0
                            for uid, u_data in pending_users.items():
                                if u_data.get("class_group") == approve_class_sel and uid in fresh_users:
                                    fresh_users[uid]["approved"] = True
                                    count += 1
                            save_json(USERS_FILE, fresh_users)
                            create_auto_backup(f"[{approve_class_sel}] 반별 승인 ({count}명)")
                            st.success("승인 완료"); st.rerun()
                    with col_p2:
                        approve_target = st.selectbox("👤 개별 선택", ["선택"] + list(pending_users.keys()), format_func=lambda x: x if x=="선택" else f"[{pending_users[x].get('class_group')}] {pending_users[x].get('name')} ({pending_users[x].get('id')})")
                        if approve_target != "선택" and st.button("✅ 개별 승인", type="primary", use_container_width=True):
                            fresh_users = load_json(USERS_FILE, {})
                            if approve_target in fresh_users: fresh_users[approve_target]["approved"] = True
                            save_json(USERS_FILE, fresh_users)
                            create_auto_backup(f"[{pending_users[approve_target].get('name')}] 개별 승인")
                            st.success("승인 완료"); st.rerun()
                    with col_p3:
                        st.write(""); st.write("")
                        if st.button("✅ 모든 대기 학생 일괄 승인", type="primary", use_container_width=True):
                            fresh_users = load_json(USERS_FILE, {})
                            for uid in pending_users.keys():
                                if uid in fresh_users: fresh_users[uid]["approved"] = True
                            save_json(USERS_FILE, fresh_users)
                            create_auto_backup("전체 대기 학생 일괄 승인")
                            st.success("승인 완료"); st.rerun()
                else: 
                    st.info("대기 중인 학생이 없습니다.")
                
                st.markdown("---")
                
                st.markdown("### ✅ 승인 완료 학생 목록 (전체 회원)")
                approved_users = {k: v for k, v in all_users.items() if v.get("approved", True) and v.get("role")=="학생"}
                if approved_users:
                    col_fa1, col_fa2 = st.columns(2)
                    with col_fa1:
                        filter_subj = st.selectbox("📘 과목 필터", ["전체 보기"] + SUBJECTS, key="filter_subj_approved")
                    with col_fa2:
                        class_options = ["전체 보기"]
                        if filter_subj != "전체 보기":
                            class_options += CLASSES_MAP.get(filter_subj, [])
                        filter_class = st.selectbox("🏫 반 필터", class_options, key="filter_class_approved")
                        
                    display_approved = []
                    for k, v in approved_users.items():
                        if filter_subj != "전체 보기" and v.get("subject") != filter_subj: continue
                        if filter_class != "전체 보기" and v.get("class_group") != filter_class: continue
                        display_approved.append({
                            "과목": v.get("subject", "-"), 
                            "반": v.get("class_group", "-"), 
                            "학번": v.get("id", "-"), 
                            "이름": v.get("name", "-"),
                            "비밀번호": v.get("password", "-")
                        })
                        
                    if display_approved:
                        df_approved = pd.DataFrame(display_approved)
                        df_approved = df_approved.sort_values(by=["과목", "반", "학번"])
                        st.dataframe(df_approved, use_container_width=True, hide_index=True)
                        st.caption(f"총 {len(display_approved)}명의 학생이 조회되었습니다.")
                    else:
                        st.info("조건에 일치하는 학생이 없습니다.")
                else:
                    st.info("현재 가입 승인된 학생이 없습니다.")
                
                st.markdown("---")

                st.markdown("### 📋 학생 회원 정보 수정 및 계정 삭제")
                search_edit = st.text_input("🔍 검색 (이름 또는 학번 입력)", key="search_edit")
                filtered_for_edit = filter_students_admin(all_users, search_edit, approved_only=False)

                if search_edit.strip() and len(filtered_for_edit) > 0:
                    options_edit = list(filtered_for_edit.keys())
                else:
                    options_edit = ["선택"] + list(filtered_for_edit.keys())

                edit_target = st.selectbox(
                    "학생 선택",
                    options_edit,
                    format_func=lambda x: "선택" if x == "선택" else f"[{filtered_for_edit[x].get('class_group')}] {filtered_for_edit[x].get('name')} ({filtered_for_edit[x].get('id')})",
                    key="edit_target_tab2"
    )

                if edit_target != "선택":
                    target_info = filtered_for_edit[edit_target]
                    with st.form("edit_student_form"):
                        e_subj = st.selectbox("과목", SUBJECTS, index=SUBJECTS.index(target_info.get("subject")) if target_info.get("subject") in SUBJECTS else 0)
                        e_cls = st.selectbox("반", CLASSES_MAP.get(e_subj, []), index=CLASSES_MAP.get(e_subj, []).index(target_info.get("class_group")) if target_info.get("class_group") in CLASSES_MAP.get(e_subj, []) else 0)
                        e_id = st.text_input("학번", value=target_info.get("id", ""))
                        e_name = st.text_input("이름", value=target_info.get("name", ""))
                        e_pw = st.text_input("비밀번호", value=target_info.get("password", ""))

                        col_btn1, col_btn2 = st.columns(2)
                        with col_btn1:
                            btn_edit = st.form_submit_button("정보 수정 적용", type="primary", use_container_width=True)
                        with col_btn2:
                            btn_delete = st.form_submit_button("❌ 학생 계정 영구 삭제", use_container_width=True)

                    # 1. 정보 수정 처리
                    if btn_edit:
                        fresh_users = load_json(USERS_FILE, {})
                        fresh_data = load_json(DATA_FILE, {})
                        if edit_target in fresh_users:
                            fresh_users[edit_target]["subject"] = e_subj
                            fresh_users[edit_target]["class_group"] = e_cls
                            fresh_users[edit_target]["id"] = e_id
                            fresh_users[edit_target]["name"] = e_name
                            fresh_users[edit_target]["password"] = e_pw
                            save_json(USERS_FILE, fresh_users)
                            create_auto_backup(f"[{e_name}] 정보 수정")
                            st.balloons()
                            st.success(f"[{e_name}] 학생의 정보가 안전하게 수정되었습니다.")

                    # 2. 영구 삭제 처리
                    if btn_delete:
                        fresh_users = load_json(USERS_FILE, {})
                        fresh_data = load_json(DATA_FILE, {})
                        if edit_target in fresh_users:
                            del fresh_users[edit_target]
                            save_json(USERS_FILE, fresh_users)
                        if edit_target in fresh_data:
                            del fresh_data[edit_target]
                            save_json(DATA_FILE, fresh_data)
                        create_auto_backup(f"[{target_info.get('name')}] 계정 삭제")
                        st.balloons()
                        st.success(f"[{target_info.get('name')}] 학생 계정이 삭제되었습니다.")
               
            with menu_tabs[3]:
                col_t, col_b = st.columns([8, 2])
                with col_t: st.markdown("### 📥 학생 학습 활동 및 제출 자료 실시간 조회")
                render_group_cleanup()
                with col_b:
                    if st.button("🔄 실시간 새로고침", key="refresh_dashboard_btn"):
                        st.balloons()
                        st.toast("최신 데이터로 새로고침되었습니다.")
                st.markdown("---")
                col_filter1, col_filter2 = st.columns(2)
                with col_filter1: view_subj = st.selectbox("과목 선택", SUBJECTS, key="view_subj_select")
                with col_filter2: view_class = st.radio("반 선택", ["전체 보기"] + CLASSES_MAP.get(view_subj, []), horizontal=True, key="view_class_select")
                view_mode = st.radio("조회 모드 선택", ["👤 특정 학생 실시간 집중 분석", "📅 항목별(수행평가) 전체 현황 (엑셀/HTML 다운로드)"], horizontal=True)
                st.markdown("---")
                all_users = load_json(USERS_FILE, {})
                learning_data = load_json(DATA_FILE, {})
                student_list = [uid for uid, info in all_users.items() if info.get("role") == "학생" and info.get("approved", True) and info.get("subject", "").strip() == view_subj.strip() and (view_class == "전체 보기" or info.get("class_group", "").strip() == view_class.strip())]
                
                if not student_list:
                    st.info("해당 조건에 등록된 학생이 없습니다.")
                else:
                    if view_mode == "👤 특정 학생 실시간 집중 분석":
                        def _build_all_zip(student_list=student_list, all_users=all_users, learning_data=learning_data, view_subj=view_subj):
                            zip_buffer = io.BytesIO()
                            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                                cfg_ = load_json(CONFIG_FILE, {})
                                acts_for_iter = cfg_.get("subject_activities", {}).get(view_subj, [])
                                for s_uid in student_list:
                                    u_info_iter = all_users[s_uid]
                                    if any(get_user_activity_data(s_uid, u_info_iter.get('id',''), view_subj, u_info_iter.get('class_group',''), act, learning_data)[1] for act in acts_for_iter):
                                        h_content = generate_portfolio_html(s_uid, u_info_iter, view_subj, cfg_, learning_data)
                                        zip_file.writestr(f"{u_info_iter.get('class_group')}_{u_info_iter.get('name')}_포트폴리오.html", h_content.encode('utf-8-sig'))
                            return zip_buffer.getvalue()
                        lazy_download("전체 포트폴리오 일괄 다운로드 (ZIP)", f"adm_zip::{view_subj}::{view_class}", _build_all_zip,
                                      f"{view_subj}_{view_class}_포트폴리오.zip", "application/zip", type="primary")
                        st.markdown("---")
                        search_student_tab4 = st.text_input("🔍 학생 검색 (이름 또는 학번 입력)", key="search_student_tab4")
        
                        filtered_student_list = [
                            uid for uid in student_list 
                            if not search_student_tab4.strip() or search_student_tab4.strip().lower() in f"{all_users.get(uid, {}).get('name', '')} {all_users.get(uid, {}).get('id', '')} {uid}".lower()
        ]

        # 검색어 입력 시 '선택' 없이 검색된 학생이 즉시 1순위로 자동 선택됨
                        if search_student_tab4.strip():
                            if filtered_student_list:
                                selected_student = st.selectbox(
                                    f"학생 선택 (검색 결과: {len(filtered_student_list)}명)",
                                    filtered_student_list,
                                    format_func=lambda x: f"[{all_users[x].get('class_group')}] {all_users[x].get('name')} ({all_users[x].get('id')})",
                                    key="selected_student_tab4_box"
                                )
                            else:
                                st.warning("검색 일치 학생이 없습니다.")
                                selected_student = None
                        else:
                            selected_student = st.selectbox(
                                "학생 선택",
                                ["선택"] + filtered_student_list,
                                format_func=lambda x: "선택" if x == "선택" else f"[{all_users[x].get('class_group')}] {all_users[x].get('name')} ({all_users[x].get('id')})",
                                key="selected_student_tab4_box"
                            )

                        if selected_student and selected_student != "선택":
                            u_info_sel = all_users[selected_student]
                            acts_for_subj = load_json(CONFIG_FILE, {}).get("subject_activities", {}).get(view_subj, [])
                            filter_act = st.selectbox("활동지 필터링", ["전체 활동지 보기"] + acts_for_subj)
                            lock_table_headers()
                            # 🔄 다른 학생을 고르면, 앞 학생 화면에 남아 있던 입력칸 값을 모두 지우고 새 학생 자료로 다시 그림
                            _view_id = f"{selected_student}|{filter_act}"
                            if st.session_state.get("_adm_view_id") != _view_id:
                                for _k in st.session_state.get("_adm_view_keys", []):
                                    if _k in st.session_state: del st.session_state[_k]
                                st.session_state["_adm_view_keys"] = []
                                st.session_state["_adm_view_id"] = _view_id
                            render_student_doctor(selected_student, u_info_sel, acts_for_subj)
                            _keys_before = set(st.session_state.keys())
                            st.session_state["_wkey_suffix"] = f"__{selected_student}"     # 이 학생 전용 입력칸으로 그리기
                            for act in acts_for_subj:
                                if filter_act != "전체 활동지 보기" and act != filter_act: continue
                                if act == ACT_3_1: render_activity1_3th(selected_student, u_info_sel, current_role)
                                elif act == ACT_3_2: render_activity2_3th(selected_student, u_info_sel, current_role)
                                elif act == ACT_3_3: render_activity3_3th(selected_student, u_info_sel, current_role)
                                elif act == ACT_2_1: render_activity1_2nd(selected_student, u_info_sel, current_role)
                                elif act == ACT_2_2: render_activity2_2nd(selected_student, u_info_sel, current_role)
                                elif act == ACT_2_3: render_activity3_2nd(selected_student, u_info_sel, current_role)
                                else: render_custom_activity(selected_student, u_info_sel, current_role, act, app_config)
                                st.session_state["_adm_view_keys"] = list(set(st.session_state.get("_adm_view_keys", [])) | (set(st.session_state.keys()) - _keys_before - {"_adm_view_keys", "_adm_view_id"}))
                                owner_key, ans = get_user_activity_data(selected_student, u_info_sel.get('id', ''), view_subj, u_info_sel.get('class_group', ''), act, learning_data)
                                if ans:
                                    lazy_download(f"[{act}] 결과물 다운로드", f"teach::{selected_student}::{act}",
                                                  (lambda a=act, x=ans, nm=u_info_sel.get('name'): generate_activity_html(a, x, nm)),
                                                  f"{u_info_sel.get('name')}_{act}.html", "text/html")
                                st.markdown("---")
                            st.session_state["_wkey_suffix"] = None
                                
                    elif view_mode == "📅 항목별(수행평가) 전체 현황 (엑셀/HTML 다운로드)":
                        acts_for_subj = load_json(CONFIG_FILE, {}).get("subject_activities", {}).get(view_subj, [])
                        if acts_for_subj:
                            selected_view = st.selectbox("수행평가 선택", acts_for_subj)
                            _iv_key = f"{view_subj}|{view_class}|{selected_view}"
                            if st.session_state.get("_item_view_open") != _iv_key:
                                st.info("학생 전체의 내용을 한꺼번에 불러오면 서버 메모리를 많이 씁니다. 필요할 때만 불러오세요.")
                                if st.button("📋 이 수행평가 전체 현황 불러오기", key="btn_item_view_open", type="primary"):
                                    st.session_state["_item_view_open"] = _iv_key
                                    st.rerun()
                            if st.session_state.get("_item_view_open") == _iv_key and st.button("✖ 전체 현황 닫기 (메모리 절약)", key="btn_item_view_close"):
                                st.session_state.pop("_item_view_open", None)
                                st.rerun()
                            if st.session_state.get("_item_view_open") == _iv_key:
                                csv_data = []
                                zip_buffer_item = io.BytesIO()
                                has_html_item = False
                                with zipfile.ZipFile(zip_buffer_item, "w", zipfile.ZIP_DEFLATED) as zf_item:
                                    for s_uid in student_list:
                                        u_info_csv = all_users[s_uid]
                                        owner_key, ans = get_user_activity_data(s_uid, u_info_csv.get('id', ''), view_subj, u_info_csv.get('class_group', ''), selected_view, learning_data)
                                        st.markdown(f"#### 👤 [{u_info_csv.get('subject')}] {u_info_csv.get('class_group')} - {u_info_csv.get('name')} ({u_info_csv.get('id')})")
                                        csv_data.append([f"■ {u_info_csv.get('class_group')} - {u_info_csv.get('name')}", ""])
                                        if not ans:
                                            st.caption("제출된 내용이 없습니다.")
                                            csv_data.append(["제출 여부", "미제출"]); csv_data.append(["---", "---"])
                                            continue
                                        act_rows = get_act_csv_rows(selected_view, ans, app_config)
                                        csv_data.extend(act_rows)
                                        for row in act_rows:
                                            q_t, a_t = str(row[0]), str(row[1])
                                            if not q_t and not a_t:
                                                continue

                                            # 제목/소제목 판별 및 위계 분리 (대괄호[] 자동 제거)
                                            if not a_t and (q_t.startswith("[") or q_t.startswith("▶")):
                                                clean_title = q_t.strip("[]")
                                                is_main = any(k in clean_title for k in [
                                                    "Step", "1. 세계 인식", "2. 특정 대륙", "3. 포용", "4. 목표", 
                                                    "모둠 구성원", "개별 정보"
                                                ])
                                           

                                                if is_main:
                                                    # 🔷 [대제목]: 큰 글씨(20px) + 파란색 + 하단 밑줄선
                                                    main_style = (
                                                        "color: #1e3a8a; font-size: 20px; font-weight: 800; "
                                                        "margin-top: 30px; margin-bottom: 12px; "
                                                        "border-bottom: 2px solid #3b82f6; padding-bottom: 6px;"
                                                    )
                                                    st.markdown(f"<h3 style='{main_style}'>{clean_title}</h3>", unsafe_allow_html=True)
                                                else:
                                                    # 🔹 [소제목]: 대괄호 제거 + 밑줄 없음 + 단정한 크기(16px) + 짙은 회색
                                                    sub_style = (
                                                        "color: #334155; font-size: 16px; font-weight: 700; "
                                                        "margin-top: 18px; margin-bottom: 8px;"
                                                    )
                                                    st.markdown(f"<div style='{sub_style}'>▶ {clean_title}</div>", unsafe_allow_html=True)
                                            elif a_t:
                                                box_style = (
                                                    "background-color: #f8f9fa; padding: 12px; "
                                                    "border-radius: 5px; border: 1px solid #e9ecef; margin-bottom: 8px;"
                                                )
                                                st.markdown(f"<div style='{box_style}'><b>{q_t}</b><br>{a_t}</div>", unsafe_allow_html=True)
                                            
                                        # 🌟 [복구된 코드] 삭제되었던 HTML 파일 생성 변수 1줄 복구!
                                        single_html = generate_activity_html(selected_view, ans, u_info_csv.get('name'))
                                    
                                        zf_item.writestr(f"{u_info_csv.get('class_group')}_{u_info_csv.get('name')}_{selected_view[:8]}.html", single_html.encode('utf-8-sig'))
                                        has_html_item = True
                                        csv_data.append(["---", "---"])
                                        
                                    
                                col_down1, col_down2 = st.columns(2)
                                with col_down1:
                                    if csv_data:
                                        df_csv = pd.DataFrame(csv_data)
                                        st.download_button(f"📊 엑셀(CSV) 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{view_subj}_{view_class}_{selected_view[:6]}.csv", mime='text/csv', type="primary", use_container_width=True, key="dl_item_csv")
                                with col_down2:
                                    if has_html_item:
                                        st.download_button(f"📦 웹문서(HTML/ZIP) 다운로드", data=zip_buffer_item.getvalue(), file_name=f"{view_subj}_{view_class}_HTML.zip", mime="application/zip", type="primary", use_container_width=True)

            # --- 💾 탭 4: 데이터 수동 백업 및 복구 ---
            with menu_tabs[4]:
                st.markdown("### 💾 시스템 데이터베이스(DB) 수동 백업 및 복구")
                
                if st.session_state.get("restore_success_manual", False):
                    st.balloons()
                    st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 데이터 복구가 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>업로드하신 파일로 시스템 데이터베이스가 안전하게 덮어쓰기 되었습니다.</p></div>", unsafe_allow_html=True)
                    st.session_state.restore_success_manual = False

                st.error("🚨 **[주의]** 데이터 복구(업로드) 시 기존 데이터는 모두 지워지고 업로드한 파일로 완전히 덮어씌워집니다. 과거 자료 복원을 원하실 때만 신중하게 작업해 주세요!")

                def safe_parse_json(uploaded_file, target_key):
                    raw_bytes = uploaded_file.getvalue()
                    parsed = None
                    for enc in ['utf-8-sig', 'utf-8', 'cp949', 'euc-kr']:
                        try:
                            text = raw_bytes.decode(enc).lstrip('\ufeff')
                            parsed = json.loads(text)
                            break
                        except: continue
                    if parsed is not None:
                        if isinstance(parsed, dict) and "timestamp" in parsed and target_key in parsed:
                            return parsed[target_key]
                        return parsed
                    return None

                col_bk1, col_bk2 = st.columns(2)
                with col_bk1:
                    st.markdown("#### 1️⃣ 현재 시스템 DB 다운로드 (백업)")
                    _stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
                    lazy_download("학생 학습 데이터 백업 (learning_data.json)", "bk_data",
                                  lambda: json.dumps(load_json(DATA_FILE, {}), ensure_ascii=False), f"learning_data_{_stamp}.json", "application/json", use_container_width=True)
                    lazy_download("회원 정보 데이터 백업 (users.json)", "bk_users",
                                  lambda: json.dumps(load_json(USERS_FILE, {}), ensure_ascii=False, indent=2), f"users_{_stamp}.json", "application/json", use_container_width=True)
                    lazy_download("시스템 설정 데이터 백업 (config.json)", "bk_config",
                                  lambda: json.dumps(load_json(CONFIG_FILE, {}), ensure_ascii=False, indent=2), f"config_{_stamp}.json", "application/json", use_container_width=True)

                with col_bk2:
                    st.markdown("#### 2️⃣ 과거 시스템 DB 불러오기 (복구)")
                    
                    st.write("📂 [1] 학생 학습 데이터 복구")
                    up_data = st.file_uploader("learning_data.json 수동 복구", type="json", key="up_data", label_visibility="collapsed")
                    if st.button("학생 학습 데이터 복구 실행", use_container_width=True):
                        if up_data:
                            parsed_data = safe_parse_json(up_data, "learning_data")
                            if parsed_data is not None:
                                STORE.snapshot_now("수동 복구 직전 상태 자동 보관")
                                save_json(DATA_FILE, parsed_data)
                                create_auto_backup("수동 학습 데이터 복구")
                                st.session_state.restore_success_manual = True; st.rerun()
                            else: 
                                st.error("❌ 올바른 json 파일이 아니거나 인코딩이 손상되었습니다.")

                    st.write("📂 [2] 회원 정보 데이터 복구")
                    up_users = st.file_uploader("users.json 수동 복구", type="json", key="up_users", label_visibility="collapsed")
                    if st.button("회원 정보 복구 실행", use_container_width=True):
                        if up_users:
                            parsed_users = safe_parse_json(up_users, "users")
                            if parsed_users is not None:
                                STORE.snapshot_now("수동 복구 직전 상태 자동 보관")
                                save_json(USERS_FILE, parsed_users)
                                create_auto_backup("수동 회원 정보 복구")
                                st.session_state.restore_success_manual = True; st.rerun()
                            else: 
                                st.error("❌ 올바른 json 파일이 아니거나 인코딩이 손상되었습니다.")

                    st.write("📂 [3] 시스템 설정 데이터 복구")
                    up_config = st.file_uploader("config.json 수동 복구", type="json", key="up_config", label_visibility="collapsed")
                    if st.button("시스템 설정 복구 실행", use_container_width=True):
                        if up_config:
                            parsed_config = safe_parse_json(up_config, "config")
                            if parsed_config is not None:
                                STORE.snapshot_now("수동 복구 직전 상태 자동 보관")
                                save_json(CONFIG_FILE, parsed_config)
                                create_auto_backup("수동 시스템 설정 복구")
                                st.session_state.restore_success_manual = True; st.rerun()
                            else: 
                                st.error("❌ 올바른 json 파일이 아니거나 인코딩이 손상되었습니다.")

            # --- 🛡️ 탭 5: 자동 백업 센터 ---
            with menu_tabs[5]:
                if st.session_state.get("restore_success_auto", False):
                    st.balloons()
                    st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 자동 복원이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>선택하신 스냅샷 시점으로 시스템이 완벽하게 롤백되었습니다.</p></div>", unsafe_allow_html=True)
                    st.session_state.restore_success_auto = False

                st.markdown("<h3 style='font-size: 26px; font-weight: 900; margin-top:20px; color:#333;'><span style='font-size: 28px;'>💾</span> 자동 백업 센터</h3>", unsafe_allow_html=True)

                # 🛟 저장소 상태(구글 시트 연결/미반영 건수/경고)
                _stt = STORE.status()
                if _stt["sheets_enabled"]:
                    if _stt["sheet_last_error"]:
                        st.warning(f"☁️ 구글 시트 반영 지연 중 (대기 {_stt['sheet_pending']}건, 자동 재시도) — {_stt['sheet_last_error']}")
                    else:
                        st.success(f"☁️ 구글 시트 실시간 보관 연결됨 · 마지막 반영 {_stt['sheet_last_ok'] or '-'} · 대기 {_stt['sheet_pending']}건")
                elif _stt["sheets_disabled_reason"]:
                    st.error(f"☁️ 구글 시트 연결 실패로 서버 파일에만 저장 중입니다. 설정을 확인해 주세요: {_stt['sheets_disabled_reason']}")
                else:
                    st.info("☁️ 구글 시트가 연결되어 있지 않아 서버 파일에만 저장 중입니다. Streamlit Cloud는 재시작 시 파일이 초기화되므로 시트 연결을 권장합니다. (안내서 참고)")
                if _stt["last_flush_error"]:
                    st.error(f"💾 서버 파일 기록 오류: {_stt['last_flush_error']}")
                for _w in _stt["warnings"]:
                    st.warning(f"🛟 {_w}")
                if _stt.get("report_last_error"):
                    st.warning(f"📊 응답 탭 갱신 오류(30초마다 재시도): {_stt['report_last_error']}")
                _mb = _rss_mb()
                st.caption(f"저장 엔진 버전: {getattr(storage, 'ENGINE_VERSION', '1.0 (재시작 필요)')} · 응답 탭 마지막 갱신: {_stt.get('report_last_ok') or '-'}"
                           f" · 서버 메모리 사용: {f'{_mb:.0f}MB / 1024MB' if _mb else '-'}")
                if _mb and _mb > 800:
                    st.warning("⚠️ 서버 메모리가 한도(1GB)에 가깝습니다. 아래 [🧹 메모리 정리]를 먼저 눌러 보세요. 그래도 높으면 학생들이 [저장하기]를 누른 뒤 수업이 없는 시간에 Reboot 해 주세요.")
                if _mb and st.button("🧹 메모리 정리 (다 쓴 메모리 반납 · 학생 자료에는 영향 없음)", key="btn_mem_release"):
                    _after = memory_release()
                    st.success(f"정리했습니다: {_mb:.0f}MB → {(_after or 0):.0f}MB")
                if _stt["sheets_enabled"] and st.button("🔄 구글 시트 응답 탭 지금 새로 쓰기", key="btn_report_refresh",
                                                        help="학생 입력이 없을 때도 응답_ 탭을 지금 앱 기준(항목 이름 등)으로 다시 씁니다. 10~20초 걸립니다."):
                    with st.spinner("응답 탭을 새로 쓰는 중입니다... (10~20초)"):
                        _ok, _err = request_report_refresh()
                    if _ok: st.success("✅ 응답 탭을 새로 썼습니다. 구글 시트 화면을 새로고침(F5)해서 확인하세요.")
                    elif _err: st.error(f"⚠️ 응답 탭 갱신 오류: {_err}")
                    else: st.info("⏳ 요청은 보냈지만 아직 끝나지 않았습니다. 1분 뒤 구글 시트를 새로고침해 보세요.")
                _too_big = any("too large" in str(x or "").lower() for x in (_stt.get("sheet_last_error"), _stt.get("report_last_error")))
                if _stt["sheets_enabled"] or _too_big:
                    with st.expander("🧰 구글 시트 용량 문제 해결 (\"This document is too large\" 오류가 날 때)", expanded=_too_big):
                        if _too_big:
                            st.error("🚨 구글 시트 문서가 용량 한도를 넘어 **시트 저장이 멈춘 상태**입니다. 학생 자료는 앱 서버에 안전하게 저장되고 있지만, "
                                     "**지금 Reboot 하면 시트에 아직 못 올린 자료가 사라집니다. 해결될 때까지 Reboot 하지 마세요.**")
                        st.markdown(f"- 시트에 아직 못 올린 자료: **{_stt.get('sheet_pending', 0)}건**  \n"
                                    f"- 영상처럼 큰 첨부 파일(약 0.5MB 초과)은 이제 시트에 올리지 않습니다. 글·표·스케치·사진은 그대로 시트에 보관됩니다.")
                        st.markdown("**방법 1. 지금 시트에서 큰 영상 빼기** — 먼저 시도해 보세요.")
                        if st.button("🧹 시트 용량 줄이기 (큰 영상 빼기 + 빈 열 지우기)", key="btn_sheet_shrink"):
                            with st.spinner("시트를 정리하는 중입니다... (1~3분)"):
                                try:
                                    _r, _c = sheet_shrink_now()
                                    STORE.sheet_backoff_until = 0.0; STORE.report_dirty = True; STORE.last_report = 0.0
                                    st.success(f"✅ 정리 완료: 큰 첨부가 든 줄 {_r}개를 가볍게 다시 쓰고, 빈 열 {_c}개를 지웠습니다. 1~2분 뒤 이 화면을 새로고침해서 '대기 0건'이 되는지 확인하세요.")
                                except Exception as _e:
                                    st.error(f"⚠️ 지금 시트에서는 정리할 수 없습니다: {type(_e).__name__}: {str(_e)[:200]}  \n→ 아래 **방법 2(새 시트로 옮기기)** 를 진행해 주세요.")
                        st.markdown("**방법 2. 새 구글 시트로 옮기기** — 방법 1이 안 되면 진행하세요. (지금 시트는 그대로 남겨 두면 예전 기록 보관용이 됩니다)")
                        _sa = (_sheets_conf() or ({}, ""))[0].get("client_email", "(secrets 의 client_email)")
                        st.markdown(f"① 새 구글 시트를 만들고 [공유]에서 아래 주소를 **편집자**로 추가하세요.  \n`{_sa}`  \n"
                                    "② 새 시트 주소(URL)를 아래 칸에 붙여 넣고 [새 시트로 옮기기]를 누르세요. 서버에 있는 최신 자료 전체가 새 시트로 올라갑니다(몇 분 걸림).  \n"
                                    "③ **반드시** Streamlit 앱 설정의 Secrets 에서 `gsheet_url` 을 새 주소로 바꿔 저장하세요. (안 바꾸면 나중에 Reboot 할 때 예전 시트에서 불러옵니다)")
                        _new_url = st.text_input("새 구글 시트 주소", key="new_sheet_url", placeholder="https://docs.google.com/spreadsheets/d/...")
                        if st.button("📦 새 시트로 옮기기", key="btn_sheet_switch", disabled=not _new_url.strip().startswith("https://docs.google.com/spreadsheets/")):
                            try:
                                _t = sheet_switch_to(_new_url)
                                st.success(f"✅ 새 시트 '{_t}' 에 연결했습니다. 몇 분 동안 자료 전체가 올라갑니다. 이 화면을 새로고침해 '대기 0건'이 되면 완료입니다.  \n"
                                           "⚠️ 이제 Secrets 의 `gsheet_url` 을 새 주소로 꼭 바꿔 주세요.")
                            except Exception as _e:
                                st.error(f"⚠️ 새 시트를 열 수 없습니다. 공유(편집자)와 주소를 확인하세요: {type(_e).__name__}: {str(_e)[:200]}")
                
                bk_files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".json")], reverse=True)
                total_bytes = sum([os.path.getsize(os.path.join(BACKUP_DIR, f)) for f in bk_files if os.path.isfile(os.path.join(BACKUP_DIR, f))])
                total_mb = round(total_bytes / (1024 * 1024), 1)
                
                current_learning_db = load_json(DATA_FILE, {})
                active_student_count = len(current_learning_db.keys())
                
                last_bk_time = "없음"
                if bk_files:
                    try:
                        with open(os.path.join(BACKUP_DIR, bk_files[0]), "r", encoding="utf-8-sig") as lf:
                            last_bk_time = json.load(lf).get("timestamp", "알수없음")
                    except: last_bk_time = "확인 불가"
                
                c_stat1, c_stat2, c_stat3, c_stat4 = st.columns(4)
                with c_stat1:
                    st.caption("마지막 백업")
                    st.markdown(f"<h2 style='color:#34495e; font-size:28px; font-weight:500; margin:0;'>{last_bk_time}</h2>", unsafe_allow_html=True)
                with c_stat2:
                    st.caption("보관 백업")
                    st.markdown(f"<h2 style='color:#34495e; font-size:28px; font-weight:500; margin:0;'>{len(bk_files)}개</h2>", unsafe_allow_html=True)
                with c_stat3:
                    st.caption("용량")
                    st.markdown(f"<h2 style='color:#34495e; font-size:28px; font-weight:500; margin:0;'>{total_mb} MB</h2>", unsafe_allow_html=True)
                with c_stat4:
                    st.caption("데이터 보유 학생")
                    st.markdown(f"<h2 style='color:#34495e; font-size:28px; font-weight:500; margin:0;'>{active_student_count}명</h2>", unsafe_allow_html=True)
                
                st.markdown("<hr style='margin:25px 0;'>", unsafe_allow_html=True)
                st.markdown("#### 🕒 백업 스냅샷 선택 및 원클릭 복원 (Rollback)")
                st.info("💡 학생 저장 내용은 저장 즉시 저널(및 연결 시 구글 시트)에 기록되고, 전체 스냅샷은 변경이 있을 때 최대 5분마다 자동 생성됩니다(최근 48개 보관). 복원 버튼을 누르면 복원 직전 상태도 자동 보관됩니다.")
                
                if bk_files:
                    bk_options = {}
                    for f_name in bk_files:
                        try:
                            with open(os.path.join(BACKUP_DIR, f_name), "r", encoding="utf-8-sig") as bf:
                                b_info = json.load(bf)
                                bk_options[f_name] = f"[{b_info.get('timestamp', '알수없음')}] {b_info.get('reason', '스냅샷')} ({f_name})"
                        except: bk_options[f_name] = f_name
                            
                    selected_bk_file = st.selectbox("복원할 시점을 선택하세요", list(bk_options.keys()), format_func=lambda x: bk_options[x], label_visibility="collapsed")
                    
                    col_r1, col_r2 = st.columns([2, 1])
                    with col_r1:
                        if st.button("⏪ 선택한 시점으로 시스템 자동 복원 (1클릭 롤백)", type="primary", use_container_width=True):
                            try:
                                STORE.snapshot_now("복원 직전 상태 자동 보관")  # 🛟 잘못 복원해도 되돌릴 수 있도록
                                with open(os.path.join(BACKUP_DIR, selected_bk_file), "r", encoding="utf-8-sig") as bf:
                                    restored_bundle = json.load(bf)
                                    if "users" in restored_bundle: save_json(USERS_FILE, restored_bundle["users"])
                                    if "learning_data" in restored_bundle: save_json(DATA_FILE, restored_bundle["learning_data"])
                                    if "config" in restored_bundle: save_json(CONFIG_FILE, restored_bundle["config"])
                                st.session_state.restore_success_auto = True
                                st.rerun()
                            except Exception as e: st.error(f"복원 중 오류 발생: {e}")
                    with col_r2:
                        if st.button("📸 지금 즉시 수동 스냅샷 생성", use_container_width=True):
                            create_auto_backup("관리자 수동 스냅샷 생성", force=True)
                            st.session_state.admin_save_success = True; st.rerun()
                else:
                    st.info("아직 생성된 백업 스냅샷이 없습니다. 학생들의 활동 제출이나 설정 저장이 일어나면 자동으로 쌓이게 됩니다.")
