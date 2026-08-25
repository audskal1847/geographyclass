import streamlit as st
import streamlit.components.v1 as components
import json
import os
import base64
import pandas as pd
import datetime
import threading
import io
import zipfile

# =====================================================================
# 🚀 기본 설정 및 페이지 구성 (사이드바 자동 열림 유지 및 버튼 색상 복원)
# =====================================================================
st.set_page_config(page_title="수업 및 활동 어시스트 프로그램", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
.stMarkdown h1 { font-size: 34px !important; font-weight: 900 !important; color: #000000 !important; margin-bottom: 20px !important; }
.stMarkdown h2 { font-size: 28px !important; font-weight: 900 !important; color: #000000 !important; margin-top: 10px !important; margin-bottom: 15px !important; padding-bottom: 8px !important; border-bottom: 2px solid #dddddd !important; }
.stMarkdown h3 { font-size: 24px !important; font-weight: 800 !important; color: #111111 !important; margin-top: 25px !important; margin-bottom: 10px !important; }
div[data-testid="stMarkdownContainer"] > p, div[data-testid="stMarkdownContainer"] > ul > li { font-size: 16px !important; font-weight: 500 !important; color: #333333 !important; line-height: 1.6 !important; }
.stMarkdown strong, .stMarkdown b { font-weight: 700 !important; color: #000000 !important; }

/* 🔴 Primary 버튼 (저장, 로그인, 제출 등) - 빨간색 */
[data-testid="stFormSubmitButton"] button, button[kind="primary"] { 
    background-color: #FF4B4B !important; border: none !important; border-radius: 8px !important; min-height: 50px !important; width: 100% !important; padding: 12px !important; 
}
[data-testid="stFormSubmitButton"] button p, button[kind="primary"] p, [data-testid="stFormSubmitButton"] button div, button[kind="primary"] div { 
    color: #ffffff !important; font-size: 18px !important; font-weight: 800 !important; 
}

/* 🔵 Secondary 버튼 (다운로드, 백업, 일반 버튼 등) - 파란색 복원 */
button[kind="secondary"] { 
    background-color: #3498db !important; border: none !important; border-radius: 8px !important; min-height: 50px !important; width: 100% !important; padding: 12px !important; 
}
button[kind="secondary"] p, button[kind="secondary"] div { 
    color: #ffffff !important; font-size: 18px !important; font-weight: 800 !important; 
}

[data-testid="stForm"] { border: none !important; box-shadow: none !important; padding: 0 !important; }
</style>
""", unsafe_allow_html=True)

# --- [0] 서버 한국시간(KST) 보정 함수 ---
def get_kst_now(): 
    return datetime.datetime.utcnow() + datetime.timedelta(hours=9)

# --- [1] 파일 경로 설정 및 상수 정의 ---
USERS_FILE = "users.json"
DATA_FILE = "learning_data.json"
CONFIG_FILE = "config.json"
UPLOAD_DIR = "uploads"
BACKUP_DIR = "backups"

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# 📌 과목 및 반 목록 세팅
SUBJECTS = [
    "3학년 여행지리", 
    "2학년 도시의 미래 탐구"
]

CLASSES_MAP = {
    "3학년 여행지리": ["3B(3-6반)", "3A(3-8반)"],
    "2학년 도시의 미래 탐구": ["2G(2-1반)", "2H(2-2반)", "2I(2-8반)"]
}

# 📌 단일 관리자 계정
ADMIN_ACCOUNTS = {
    "audskal": {"pw": "1847", "name": "김명남(관리자)"}
}

# 📌 학년별 하드코딩 기본 활동지 6종
ACT_3_1 = "[3학년] 수행평가 1 - 영상으로 떠나는 여행"
ACT_3_2 = "[3학년] 수행평가 2 - 나를 성장시킨 장소 지도 만들기"
ACT_3_3 = "[3학년] 수행평가 3 - 나의 세계관에 대해 알아가는 '여행'"
ACT_2_1 = "[2학년] 수행평가 1 - 도시 '밈' 해석을 통한 도시성과 생활양식 탐구"
ACT_2_2 = "[2학년] 수행평가 2 - 내가 설계하는 N분 도시 with 파리의 15분 도시설계"
ACT_2_3 = "[2학년] 수행평가 3 - 빛으로 우리 지역을 말하다 - 내가 만드는 미디어 파사드"

ACTIVITIES = [ACT_3_1, ACT_3_2, ACT_3_3, ACT_2_1, ACT_2_2, ACT_2_3]
TIME_OPTIONS = [f"{str(h).zfill(2)}:{str(m).zfill(2)}" for h in range(24) for m in range(0, 60, 10)]
db_lock = threading.RLock()

def get_time_index(t_str): 
    if t_str in TIME_OPTIONS: return TIME_OPTIONS.index(t_str)
    return 0

def encode_token(user_key): 
    return base64.b64encode(user_key.encode('utf-8')).decode('utf-8')

def decode_token(token):
    try: 
        return base64.b64decode(token.encode('utf-8')).decode('utf-8')
    except: 
        return None

def change_page(page_name):
    st.session_state.current_page = page_name
    st.query_params["current_page"] = page_name
    if st.session_state.get("logged_in") and st.session_state.get("user_info"):
        st.query_params["session_token"] = encode_token(st.session_state.user_info["user_key"])
    st.rerun()

# 🌟 개인정보 처리방침 공통 모듈
def render_privacy_policy():
    with st.expander("📜 개인정보 처리방침 (수업용 웹 앱)", expanded=False):
        st.markdown("""
        **[신선여자고등학교 수업용 웹 앱 개인정보 처리방침]**
        **1. 개인정보 수집 목적**: 교과 수업 운영, 학생 수행평가 과제물 제출/취합, 피드백 제공 및 학교생활기록부 기재 증빙 자료 활용
        **2. 수집 항목**: 필수 항목(과목, 반, 학번, 이름, 비밀번호, 과제물 데이터). ※ 주민등록번호 등 민감정보 일체 수집 불가
        **3. 보유 및 이용 기간**: 해당 학년도 교육과정 종료 시(익년 2월 말) 데이터 일괄 파기
        **4. 안전성 확보 조치**: 비밀번호 암호화/비노출, 교사-학생 권한 분리, 실시간 자동 스냅샷 백업 시스템 운영
        **5. 권리 행사**: 학생은 언제든지 자신의 개인정보 열람/정정/삭제를 요구할 수 있으며 담당 교사가 즉시 처리함.
        **6. 만 14세 미만 아동 보호**: 고등학교 재학생 전용 시스템으로 해당 없음.
        **7. 책임자**: 교사 김명남 / 신선여자고등학교
        **8. 제3자 제공**: 수집된 학생 정보를 외부에 절대 제공하거나 위탁하지 않음.
        """)

# --- [📌 반별 수행평가 타이머 및 마감 제어 로직] ---
def check_active(act_name, class_group):
    config = load_json(CONFIG_FILE, {})
    deadlines = config.get("deadlines", {}).get(act_name, {}).get(class_group, {})
    
    if not deadlines:
        return True, "💡 교사가 아직 수업 시간표를 설정하지 않았습니다. (현재 자유 입력 가능)"

    final_dl_str = deadlines.get("final_dl", "2030-12-31 23:59")
    try: 
        final_dl = datetime.datetime.strptime(final_dl_str, "%Y-%m-%d %H:%M")
    except: 
        final_dl = datetime.datetime.max

    now = get_kst_now()

    if now > final_dl: 
        return False, f"🚫 최종 제출 기한({final_dl_str})이 마감되어 더 이상 작성하거나 수정할 수 없습니다."

    slots = deadlines.get("slots", [])
    day_map = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금", 5: "토", 6: "일"}
    current_day = day_map[now.weekday()]
    current_time = now.time()

    schedule_strs = []
    is_time_match = False
    
    for slot in slots:
        if slot['day'] != "선택안함":
            p_str = f" {slot.get('period', '')}" if slot.get('period', '') and slot.get('period') != "선택안함" else ""
            schedule_strs.append(f"{slot['day']}요일{p_str} {slot['start']} - {slot['end']}")
            if slot['day'] == current_day:
                try:
                    st_time = datetime.datetime.strptime(slot["start"], "%H:%M").time()
                    en_time = datetime.datetime.strptime(slot["end"], "%H:%M").time()
                    if st_time <= current_time <= en_time: 
                        is_time_match = True
                except: 
                    continue

    sched_display = ", ".join(schedule_strs) if schedule_strs else "설정된 수업 시간 없음"
    
    if is_time_match: 
        return True, "✅ 현재 수업 시간입니다. 정상적으로 작성하고 저장(제출)할 수 있습니다."
    else: 
        return False, f"⏳ 현재는 정해진 수업 시간이 아닙니다. 지정된 수업 시간에만 입력할 수 있습니다.\n\n(나의 주간 수업 시간: {sched_display} / 최종 기한: {final_dl_str})"

# 🌟 특정 학생 개별 예외(연장) 시간 확인 기능
def check_active_with_exception(act_name, class_group, user_key):
    config = load_json(CONFIG_FILE, {})
    exceptions = config.get("exceptions", {}).get(act_name, {})
    if user_key in exceptions:
        try:
            exc_dl = datetime.datetime.strptime(exceptions[user_key], "%Y-%m-%d %H:%M")
            if get_kst_now() <= exc_dl: 
                return True, f"✅ [개별 기한 연장] 특별 권한으로 정상 작성 및 제출이 가능합니다. (마감: {exceptions[user_key]})"
            else: 
                return False, f"🚫 [개별 연장 기한 마감] 연장된 기한({exceptions[user_key]})이 지났습니다."
        except: pass
    return check_active(act_name, class_group)

# --- [📌 반별 수행평가 공개/비공개 여부 확인 함수] ---
def is_act_visible_for_class(act_name, class_group, config):
    vis_data = config.get("activity_visibility", {}).get(act_name, {})
    if isinstance(vis_data, dict):
        return vis_data.get(class_group, True)
    elif isinstance(vis_data, bool):
        return vis_data
    return True

# 🌟 특정 학생 예외(연장) 시 화면 강제 노출 기능
def is_act_visible_for_user(act_name, class_group, user_key, config):
    exceptions = config.get("exceptions", {}).get(act_name, {})
    if user_key in exceptions:
        try:
            if get_kst_now() <= datetime.datetime.strptime(exceptions[user_key], "%Y-%m-%d %H:%M"):
                return True
        except: pass
    return is_act_visible_for_class(act_name, class_group, config)

# --- [2] 데이터 입출력 및 자동 백업 함수 ---
def load_json(file_path, default_value):
    with db_lock:
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f: 
                json.dump(default_value, f, ensure_ascii=False, indent=4)
            return default_value
        for _ in range(5):
            try:
                with open(file_path, "r", encoding="utf-8") as f: 
                    return json.load(f)
            except Exception:
                import time
                time.sleep(0.1)
        return default_value

def save_json(file_path, data):
    with db_lock:
        with open(file_path, "w", encoding="utf-8") as f: 
            json.dump(data, f, ensure_ascii=False, indent=4)

# 🌟 [자동 백업 센터: 스냅샷 생성 함수]
def create_auto_backup(reason="자동 스냅샷"):
    with db_lock:
        try:
            now_str = get_kst_now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(BACKUP_DIR, f"backup_{now_str}.json")
            backup_bundle = {
                "timestamp": get_kst_now().strftime("%Y-%m-%d %H:%M:%S"),
                "reason": reason,
                "users": load_json(USERS_FILE, {}),
                "learning_data": load_json(DATA_FILE, {}),
                "config": load_json(CONFIG_FILE, {})
            }
            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump(backup_bundle, f, ensure_ascii=False, indent=2)
            
            all_bks = sorted([os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".json")])
            if len(all_bks) > 30:
                for old_bk in all_bks[:-30]:
                    try: os.remove(old_bk)
                    except: pass
        except Exception:
            pass

def init_system():
    with db_lock:
        users = load_json(USERS_FILE, {})
        users_changed = False
        for adm_id, adm_info in ADMIN_ACCOUNTS.items():
            if adm_id not in users or users[adm_id].get("password") != adm_info["pw"]:
                users[adm_id] = {"id": adm_id, "password": adm_info["pw"], "name": adm_info["name"], "role": "관리자", "subject": "전체", "class_group": "관리자", "approved": True}
                users_changed = True
                
        keys_to_delete = [k for k in users.keys() if users[k].get("role") == "관리자" and k not in ADMIN_ACCOUNTS]
        for k in keys_to_delete:
            del users[k]
            users_changed = True

        if users_changed: 
            save_json(USERS_FILE, users)
        
        current_config = load_json(CONFIG_FILE, {})
        needs_update = False
        if "materials" not in current_config: current_config["materials"] = []; needs_update = True
        if "notices" not in current_config: current_config["notices"] = []; needs_update = True
        if "custom_blocks" not in current_config: current_config["custom_blocks"] = []; needs_update = True
        if "dynamic_links" not in current_config: current_config["dynamic_links"] = []; needs_update = True
        if "activity_visibility" not in current_config: current_config["activity_visibility"] = {}; needs_update = True
        
        # 🌟 예외 저장소 초기화
        if "exceptions" not in current_config: current_config["exceptions"] = {}; needs_update = True
            
        if "subject_activities" not in current_config:
            current_config["subject_activities"] = {
                "3학년 여행지리": [ACT_3_1, ACT_3_2, ACT_3_3],
                "2학년 도시의 미래 탐구": [ACT_2_1, ACT_2_2]
            }
            needs_update = True
            
        if ACT_2_3 not in current_config["subject_activities"].get("2학년 도시의 미래 탐구", []):
            if "2학년 도시의 미래 탐구" not in current_config["subject_activities"]:
                current_config["subject_activities"]["2학년 도시의 미래 탐구"] = []
            current_config["subject_activities"]["2학년 도시의 미래 탐구"].append(ACT_2_3)
            needs_update = True
        
        if "custom_forms" not in current_config: current_config["custom_forms"] = {}; needs_update = True
        if "deadlines" not in current_config: current_config["deadlines"] = {}; needs_update = True
            
        for k in ["tabs", "pdfs", "questions"]:
            if k in current_config: 
                del current_config[k]
                needs_update = True
                
        if needs_update: 
            save_json(CONFIG_FILE, current_config)

# 📌 카테고리 병합(Rowspan) 처리용 HTML 생성 함수
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
                if not str(df_records[j].get("카테고리", "")).strip():
                    span += 1
                else:
                    break
            html += f"<tr><td rowspan='{span}' style='text-align:center; font-weight:bold; vertical-align:middle; border:1px solid #bdc3c7; padding:10px;'>{cat}</td>"
        else:
            html += "<tr>"
        html += f"<td style='border:1px solid #bdc3c7; padding:10px;'>{row.get('코드','')}</td><td style='text-align:left; border:1px solid #bdc3c7; padding:10px;'>{row.get('세부 개조 항목','')}</td><td style='border:1px solid #bdc3c7; padding:10px;'>{row.get('비용','')}</td></tr>"
        i += 1
    html += "</table><br>"
    return html

# 📌 모둠원 데이터 자동 연동 스캐너 함수
def get_user_activity_data(user_key, u_id, u_subj, u_class, act_name, learning_data):
    if act_name in [ACT_2_1, ACT_2_2]:
        u_id_str = str(u_id).strip()
        if not u_id_str:
            return user_key, learning_data.get(user_key, {}).get(act_name, {})
            
        own_data = learning_data.get(user_key, {}).get(act_name, {})
        if str(own_data.get("m1_id", "")).strip() == u_id_str:
            return user_key, own_data
            
        for k, acts in learning_data.items():
            if k.startswith(f"{u_subj}_{u_class}_"):
                a_data = acts.get(act_name, {})
                members = [str(a_data.get(f"m{i}_id", "")).strip() for i in range(1, 5)]
                if u_id_str in members:
                    return k, a_data
                    
    return user_key, learning_data.get(user_key, {}).get(act_name, {})

# --- [엑셀(CSV) 변환을 위한 데이터 추출 공통 함수] ---
def get_act_csv_rows(selected_view, ans, config=None):
    csv_data = []
    if selected_view == ACT_3_1:
        csv_data.append(["[1. 자신이 선택한 영상에 대한 첫번째 질문]", ""])
        csv_data.append(["1. 영상의 제목", ans.get("a1_1", "")])
        csv_data.append(["2. 영상에 등장하는 국가 혹은 지역", ans.get("a1_2", "")])
        csv_data.append(["3. 해당 영상을 선택하게 된 이유", ans.get("a1_3", "")])
        csv_data.append(["", ""])
        csv_data.append(["[2. 자신이 선택한 영상에 대한 두 번째 질문]", ""])
        csv_data.append(["1. 첫 느낌", ans.get("a2_1", "")])
        csv_data.append(["2. 인상적이었던 장소 혹은 공간", ans.get("a2_2_1", "")])
        csv_data.append(["이유", ans.get("a2_2_2", "")])
        csv_data.append(["3. 누구에게 추천", ans.get("a2_3_1", "")])
        csv_data.append(["추천하는 이유", ans.get("a2_3_2", "")])
        csv_data.append(["4. 나만의 감상평", ans.get("a2_4", "")])
        csv_data.append(["", ""])
        csv_data.append(["[5. 만일 내가 영상 속 지역을 배경으로 영상을 찍는다면?]", ""])
        csv_data.append(["1) 영상의 제목", ans.get("a3_1", "")])
        csv_data.append(["2) 주요 컨셉 혹은 느낌", ans.get("a3_2", "")])
        csv_data.append(["3) 누구와 함께 가고 싶은가?", ans.get("a3_3", "")])
        csv_data.append(["4) 그 이유는?", ans.get("a3_4", "")])
        csv_data.append(["5) 가장 해 보고 싶은 것", ans.get("a3_5", "")])
        csv_data.append(["6) 그 이유는?", ans.get("a3_6", "")])
        csv_data.append(["7) 꼭 넣고 싶은 장소 혹은 공간", ans.get("a3_7", "")])
        csv_data.append(["8) 그 이유는?", ans.get("a3_8", "")])
        csv_data.append(["9) 썸네일 영상 기획", ans.get("a3_9", "")])
        csv_data.append(["10) 어울리는 BGM", ans.get("a3_10", "")])
        csv_data.append(["11) BGM 선택 이유", ans.get("a3_11", "")])
    elif selected_view == ACT_3_2:
        csv_data.append(["1-1) 나에게 편안함을 주는 장소", ans.get("q1_1", "")])
        csv_data.append(["1-2) 편안함을 주는 이유", ans.get("q1_2", "")])
        csv_data.append(["2-1) 자신의 성격", ans.get("q2_1", "")])
        csv_data.append(["2-2) 성격 형성에 영향을 준 장소", ans.get("q2_2", "")])
        csv_data.append(["2-3) 그 이유", ans.get("q2_3", "")])
        csv_data.append(["3-1) 자신의 장점", ans.get("q3_1", "")])
        csv_data.append(["3-2) 장점 형성에 영향을 준 장소", ans.get("q3_2", "")])
        csv_data.append(["3-3) 그 이유", ans.get("q3_3", "")])
        csv_data.append(["4-1) 내가 성장함에 있어 영향을 준 장소", ans.get("q4_1", "")])
        csv_data.append(["4-2) 어떤 면에서 영향을 주었는가", ans.get("q4_2", "")])
        csv_data.append(["5-1) 지금 나의 목표", ans.get("q5_1", "")])
        csv_data.append(["5-2) 목표 설정에 영향을 준 장소", ans.get("q5_2", "")])
        csv_data.append(["6-1) 소중한 사람에게 소개해 주고 싶은 장소", ans.get("q6_1", "")])
        csv_data.append(["6-2) 그 이유", ans.get("q6_2", "")])
        csv_data.append(["7-1) 나만의 비밀 장소", ans.get("q7_1", "")])
        csv_data.append(["7-2) 그 이유", ans.get("q7_2", "")])
        csv_data.append(["8-1) 다시 가 보고 싶은 과거의 장소", ans.get("q8_1", "")])
        csv_data.append(["8-2) 그 이유", ans.get("q8_2", "")])
    elif selected_view == ACT_3_3:
        csv_data.append(["[1. 세계 인식 수준에 대한 확인]", ""])
        csv_data.append(["1) 대륙별 관심도 및 지식 수준 체크", ""])
        for row in ans.get("s1_df", []): 
            csv_data.append([row.get("대륙", ""), f"관심도: {row.get('관심도', '')} / 지식수준: {row.get('지식수준', '')}"])
        csv_data.append(["2) 특정 국가에 대한 기억과 인상 분석 (직접경험)", ""])
        for row in ans.get("direct_df", []): 
            csv_data.append([row.get("여행해 본 국가", ""), row.get("해당 국가에 대한 구체적인 기억 혹은 인상", "")])
        csv_data.append(["간접경험 (영화/드라마)", ans.get("ind1", "")])
        csv_data.append(["간접경험 (음악/연예인)", ans.get("ind2", "")])
        csv_data.append(["간접경험 (음식)", ans.get("ind3", "")])
        csv_data.append(["3) 꼭 가 보고 싶은 Top 5 국가와 그 이유", ""])
        for row in ans.get("top5_want", []): 
            csv_data.append([row.get("국가 혹은 지역", ""), row.get("이유", "")])
        csv_data.append(["4) 절대 가고 싫은 Top 5 국가와 그 이유", ""])
        for row in ans.get("top5_notwant", []): 
            csv_data.append([row.get("국가 혹은 지역", ""), row.get("이유", "")])
        csv_data.append(["", ""])
        csv_data.append(["[2. 특정 대륙/국가에 대한 자신의 편견과 고정관념]", ""])
        csv_data.append(["1) 국가별 한 단어 라벨링", ""])
        for row in ans.get("label_df", []): 
            csv_data.append([row.get("가 보고 싶은 국가", ""), f"라벨: {row.get('한 단어 라벨', '')} / 싫은 국가: {row.get('가고 싶지 않은 국가', '')} / 라벨(부정): {row.get('한 단어 라벨(부정)', '')}"])
        csv_data.append(["2) 개인적으로 가장 강한 편견을 가진 국가", ""])
        for row in ans.get("prej_df", []): 
            csv_data.append([row.get("국가명", ""), f"편견 내용: {row.get('편견 내용', '')} / 형성 과정: {row.get('편견 형성 과정 혹은 이유', '')}"])
        csv_data.append(["3) 미디어와 교육의 영향으로 인한 인식 발견", ""])
        csv_data.append(["뉴스에서 접하는 국가들", ans.get("media1_1", "")])
        csv_data.append(["뉴스에서의 이미지", ans.get("media1_2", "")])
        csv_data.append(["영화/드라마에서 접하는 국가들", ans.get("media2_1", "")])
        csv_data.append(["영화/드라마에서의 이미지", ans.get("media2_2", "")])
        csv_data.append(["학교에서 배운 국가들", ans.get("media3_1", "")])
        csv_data.append(["학교에서 배운 지식", ans.get("media3_2", "")])
        csv_data.append(["4) 부정확한 정보나 과장된 인식 발견", ""])
        for row in ans.get("fake_df", []): 
            csv_data.append([row.get("국가명", ""), f"잘못 알고 있던 내용: {row.get('잘못 알고 있었던 내용', '')} / 실제 사실: {row.get('실제 사실', '')}"])
        csv_data.append(["5) 우월감이나 차별 의식 점검", ""])
        for row in ans.get("discrim_df", []): 
            csv_data.append([row.get("어떤 국가에 대해?", ""), f"어떤 측면에서: {row.get('어떤 측면에서', '')} / 이유: {row.get('그 이유', '')}"])
        csv_data.append(["", ""])
        csv_data.append(["[3. 포용적이고 균형잡힌 세계관을 위한 노력]", ""])
        for row in ans.get("change_df", []): 
            csv_data.append([row.get("어떤 국가에 대해?", ""), f"현재 편견: {row.get('현재의 편견', '')} / 정보 수집 계획: {row.get('올바른 정보를 찾기 위한 계획', '')}"])
        csv_data.append(["가장 무관심했던 대륙/국가", ""])
        for row in ans.get("ignore_df", []): 
            csv_data.append([row.get("선택 대륙/국가", ""), f"무관심 이유: {row.get('무관심 이유', '')} / 관심 확장 방법: {row.get('관심 확장을 위한 정보 수집 방법', '')}"])
        csv_data.append(["서구 중심적 시각 벗어나기", ""])
        for row in ans.get("western_df", []): 
            csv_data.append([row.get("현재 가지고 있는 서구 중심적 시각", ""), f"개선 방법: {row.get('개선 방법', '')}"])
        csv_data.append(["", ""])
        csv_data.append(["[4. 목표로 하는 세계관]", ""])
        csv_data.append(["어떤 사람이 되고 싶은가?", ans.get("goal_1", "")])
        csv_data.append(["어떤 세계관을 갖고 싶은가?", ans.get("goal_2", "")])
    elif selected_view == ACT_2_1:
        csv_data.append(["모둠 구성원", f"1: {ans.get('m1_id','')} {ans.get('m1_name','')} / 2: {ans.get('m2_id','')} {ans.get('m2_name','')} / 3: {ans.get('m3_id','')} {ans.get('m3_name','')} / 4: {ans.get('m4_id','')} {ans.get('m4_name','')}"])
        csv_data.append(["1. 밈 수집", ans.get("step1_1", "")])
        csv_data.append(["2. 주관적 이미지", ans.get("step1_2", "")])
        csv_data.append(["3. 특별한 장소", ans.get("step1_3", "")])
        csv_data.append(["4. 감정/생각", ans.get("step1_4", "")])
        csv_data.append(["탐구 시기", ans.get("step2_1_period", "")])
        csv_data.append(["핵심 공간", ans.get("step2_1_space", "")])
        csv_data.append(["객관적 특징", ans.get("step2_1_feat", "")])
        csv_data.append(["객관적 지표", ans.get("step2_3", "")])
        for row in ans.get("step3_df", []): 
            csv_data.append([row.get("거주 적합성 요인", ""), f"별점: {row.get('만족도 점수', '')} / 평가: {row.get('한 줄 평가', '')}"])
        csv_data.append(["1. 기존 프레임", ans.get("step4_1", "")])
        csv_data.append(["2. 지리적 본질", ans.get("step4_2", "")])
        csv_data.append(["3. 슬로건", ans.get("step4_3", "")])
        csv_data.append(["4. 개선 아이디어", ans.get("step4_4", "")])
    elif selected_view == ACT_2_2:
        csv_data.append(["모둠 구성원", f"1: {ans.get('m1_id','')} {ans.get('m1_name','')} / 2: {ans.get('m2_id','')} {ans.get('m2_name','')} / 3: {ans.get('m3_id','')} {ans.get('m3_name','')} / 4: {ans.get('m4_id','')} {ans.get('m4_name','')}"])
        csv_data.append(["1. 대상 지역", ans.get("step1_1", "")])
        for row in ans.get("step1_2_df", []): 
            csv_data.append([row.get("구분", ""), f"항목: {row.get('필수 서비스 항목', '')} / 충분: {row.get('충분', '')} / 부족: {row.get('부족 or 없음', '')}"])
        csv_data.append(["문제점 1", ans.get("step1_3_1", "")])
        csv_data.append(["문제점 2", ans.get("step1_3_2", "")])
        csv_data.append(["문제점 3", ans.get("step1_3_3", "")])
        csv_data.append(["[도시 개조 포인트 (학생 추가 포함)]", ""])
        csv_data.append(["카테고리", "코드", "세부 개조 항목", "비용"])
        for row in ans.get("step2_point_df", []): 
            csv_data.append([row.get("카테고리", ""), row.get("코드", ""), row.get("세부 개조 항목", ""), row.get("비용", "")])
        for row in ans.get("step2_df", []): 
            csv_data.append([f"트레이드오프 순번 {row.get('순번', '')}", f"코드: {row.get('선택 코드', '')} / 버릴공간: {row.get('버릴 공간', '')} / 포인트: {row.get('사용 포인트', '')} / 재설계: {row.get('공간 재설계 이유 및 기대효과', '')}"])
        if ans.get("file_before_data") or ans.get("img_before"): 
            csv_data.append(["변경 전 자료", f"제출 완료 ({ans.get('file_before_name', '스케치.png')})"])
        if ans.get("file_after_data") or ans.get("img_after"): 
            csv_data.append(["변경 후 자료", f"제출 완료 ({ans.get('file_after_name', '스케치.png')})"])
        csv_data.append(["1. 슬로건", ans.get("step4_1", "")])
        csv_data.append(["2. 공간 문제", ans.get("step4_2", "")])
        csv_data.append(["3. 버리고 채운 것", ans.get("step4_3", "")])
        csv_data.append(["4. 일상 변화", ans.get("step4_4", "")])
    elif selected_view == ACT_2_3:
        csv_data.append(["개별 정보", f"학번: {ans.get('ind_id', '')} / 이름: {ans.get('ind_name', '')}"])
        csv_data.append(["희망 진로 혹은 계열", ans.get("ind_career", "")])
        csv_data.append(["[Step 1. 우리 지역 정체성 자원 발굴 및 팩트 체크]", ""])
        for row in ans.get("step1_df", []): 
            csv_data.append([row.get("구분", ""), f"키워드: {row.get('내가 찾은 정체성 키워드 혹은 문장', '')} / 근거: {row.get('근거가 되는 사실·통계·사건', '')} / 출처: {row.get('출처(기관명/자료명/연도)', '')}"])
        csv_data.append(["최종 선택 키워드", ans.get("step1_keyword", "")])
        csv_data.append(["단 하나의 메시지", ans.get("step1_message", "")])
        csv_data.append(["[Step 2. 캔버스 선정]", ""])
        for row in ans.get("step2_df", []): 
            csv_data.append([row.get("건물명", ""), f"벽면: {row.get('벽면 조건', '')} / 관람: {row.get('관람 조건', '')} / 접근성: {row.get('접근성', '')} / 제약: {row.get('예상 제약', '')} / 연관성: {row.get('정체성 연관성', '')} / 적합도: {row.get('적합도(별점)', '')}"])
        csv_data.append(["최종 선정 건물", ans.get("step2_final_building", "")])
        csv_data.append(["선정 이유", ans.get("step2_reason", "")])
        csv_data.append(["[Step 3. 주어진 조건 진단 및 대응 설계]", ""])
        for row in ans.get("step3_df", []): 
            csv_data.append([row.get("조건 영역", ""), f"실제조건: {row.get('현장의 실제 조건 (확인한 사실)', '')} / 미치는 영향: {row.get('작품에 미치는 영향', '')} / 대응 방안: {row.get('나의 대응 방안', '')}"])
        csv_data.append(["[Step 4. 작품 스토리보드 4컷]", ""])
        for i in range(1, 5):
            c_desc = ans.get(f'cut_{i}_desc', '')
            c_name = ans.get(f'cut_{i}_file_name', '')
            csv_data.append([f"컷 {i}", f"설명: {c_desc} / 파일: {c_name if c_name else '첨부없음'}"])
        csv_data.append(["[Step 5. 작품 설명 카드]", ""])
        csv_data.append(["제목", ans.get("step5_title", "")])
        csv_data.append(["장소", ans.get("step5_place", "")])
        csv_data.append(["개요", ans.get("step5_summary", "")])
        csv_data.append(["정체성 반영", ans.get("step5_identity", "")])
        csv_data.append(["조건 반영", ans.get("step5_condition", "")])
        csv_data.append(["남길 변화", ans.get("step5_change", "")])
        csv_data.append(["[Step 6. 생성형 AI 활용 및 성찰]", ""])
        for row in ans.get("step6_chk_df", []): 
            csv_data.append([row.get("점검 항목", ""), "확인됨" if row.get("확인") else "미확인"])
        for row in ans.get("step6_ai_df", []): 
            csv_data.append([row.get("사용한 도구명", ""), f"프롬프트: {row.get('입력한 프롬프트', '')} / 수정내용: {row.get('AI 결과물을 내가 수정·판단한 내용', '')}"])
        csv_data.append(["활동 성찰", ans.get("step6_reflection", "")])
    else:
        c_form = config.get("custom_forms", {}).get(selected_view, []) if config else []
        for q in c_form:
            csv_data.append([q["label"], ans.get(q["id"], "")])
    return csv_data

# --- [공통 HTML 포트폴리오 생성기] ---
def generate_html_content(act_name, ans, config=None):
    if config is None:
        config = load_json(CONFIG_FILE, {})
        
    html = ""
    if act_name == ACT_3_1:
        html += "<h3>1. 자신이 선택한 영상에 대한 첫번째 질문</h3><table>"
        html += f"<tr><th>영상의 제목</th><td>{ans.get('a1_1','')}</td></tr>"
        html += f"<tr><th>등장하는 국가/지역</th><td>{ans.get('a1_2','')}</td></tr>"
        html += f"<tr><th>선택 이유</th><td>{ans.get('a1_3','')}</td></tr></table>"
        html += "<h3>2. 자신이 선택한 영상에 대한 두 번째 질문</h3><table>"
        html += f"<tr><th>첫 느낌</th><td>{ans.get('a2_1','')}</td></tr>"
        html += f"<tr><th>인상적이었던 장소/공간</th><td>{ans.get('a2_2_1','')}</td></tr>"
        html += f"<tr><th>그 이유</th><td>{ans.get('a2_2_2','')}</td></tr>"
        html += f"<tr><th>누구에게 추천?</th><td>{ans.get('a2_3_1','')}</td></tr>"
        html += f"<tr><th>추천 이유</th><td>{ans.get('a2_3_2','')}</td></tr>"
        html += f"<tr><th>나만의 감상평</th><td>{ans.get('a2_4','')}</td></tr></table>"
        html += "<h3>5. 만일 내가 영상 속 지역을 배경으로 영상을 찍는다면?</h3><table>"
        html += f"<tr><th>1) 영상의 제목</th><td>{ans.get('a3_1','')}</td></tr>"
        html += f"<tr><th>2) 주요 컨셉/느낌</th><td>{ans.get('a3_2','')}</td></tr>"
        html += f"<tr><th>3) 누구와 함께?</th><td>{ans.get('a3_3','')}</td></tr>"
        html += f"<tr><th>4) 그 이유는?</th><td>{ans.get('a3_4','')}</td></tr>"
        html += f"<tr><th>5) 가장 해보고 싶은 것?</th><td>{ans.get('a3_5','')}</td></tr>"
        html += f"<tr><th>6) 그 이유는?</th><td>{ans.get('a3_6','')}</td></tr>"
        html += f"<tr><th>7) 꼭 넣고 싶은 장소/공간</th><td>{ans.get('a3_7','')}</td></tr>"
        html += f"<tr><th>8) 그 이유는?</th><td>{ans.get('a3_8','')}</td></tr>"
        html += f"<tr><th>9) 썸네일 영상 기획</th><td>{ans.get('a3_9','')}</td></tr>"
        html += f"<tr><th>10) 어울리는 BGM</th><td>{ans.get('a3_10','')}</td></tr>"
        html += f"<tr><th>11) BGM 선택 이유</th><td>{ans.get('a3_11','')}</td></tr></table>"

    elif act_name == ACT_3_2:
        html += "<table>"
        html += f"<tr><th>1-1) 편안함을 주는 장소</th><td>{ans.get('q1_1','')}</td></tr>"
        html += f"<tr><th>1-2) 편안함을 주는 이유</th><td>{ans.get('q1_2','')}</td></tr>"
        html += f"<tr><th>2-1) 자신이 생각하기에 자신의 성격은?</th><td>{ans.get('q2_1','')}</td></tr>"
        html += f"<tr><th>2-2) 성격 형성에 영향을 준 장소</th><td>{ans.get('q2_2','')}</td></tr>"
        html += f"<tr><th>2-3) 그 이유</th><td>{ans.get('q2_3','')}</td></tr>"
        html += f"<tr><th>3-1) 자신이 생각하기에 자신의 장점은?</th><td>{ans.get('q3_1','')}</td></tr>"
        html += f"<tr><th>3-2) 장점 형성에 영향을 준 장소</th><td>{ans.get('q3_2','')}</td></tr>"
        html += f"<tr><th>3-3) 그 이유</th><td>{ans.get('q3_3','')}</td></tr>"
        html += f"<tr><th>4-1) 성장함에 있어 영향을 준 장소</th><td>{ans.get('q4_1','')}</td></tr>"
        html += f"<tr><th>4-2) 어떤 면에서 영향을 주었는가</th><td>{ans.get('q4_2','')}</td></tr>"
        html += f"<tr><th>5-1) 지금 나의 목표</th><td>{ans.get('q5_1','')}</td></tr>"
        html += f"<tr><th>5-2) 목표 설정에 영향을 준 장소</th><td>{ans.get('q5_2','')}</td></tr>"
        html += f"<tr><th>6-1) 소중한 사람에게 소개해 주고 싶은 장소</th><td>{ans.get('q6_1','')}</td></tr>"
        html += f"<tr><th>6-2) 그 이유</th><td>{ans.get('q6_2','')}</td></tr>"
        html += f"<tr><th>7-1) 나만의 비밀 장소</th><td>{ans.get('q7_1','')}</td></tr>"
        html += f"<tr><th>7-2) 그 이유</th><td>{ans.get('q7_2','')}</td></tr>"
        html += f"<tr><th>8-1) 과거로 돌아간다면 가보고 싶은 장소</th><td>{ans.get('q8_1','')}</td></tr>"
        html += f"<tr><th>8-2) 그 이유</th><td>{ans.get('q8_2','')}</td></tr></table>"
        
    elif act_name == ACT_3_3:
        html += "<h3>1. 세계 인식 수준에 대한 확인</h3>"
        html += "<h4>1) 대륙별 관심도 및 지식 수준 체크</h4><table><tr><th>대륙</th><th>관심도</th><th>지식수준</th></tr>"
        for row in ans.get("s1_df", []): 
            html += f"<tr><td>{row.get('대륙','')}</td><td>{row.get('관심도','')}</td><td>{row.get('지식수준','')}</td></tr>"
        html += "</table><h4>2) 특정 국가에 대한 기억과 인상 분석</h4><h5>[직접 경험]</h5><table><tr><th>여행해 본 국가</th><th>구체적인 기억 혹은 인상</th></tr>"
        for row in ans.get("direct_df", []): 
            html += f"<tr><td>{row.get('여행해 본 국가','')}</td><td>{row.get('해당 국가에 대한 구체적인 기억 혹은 인상','')}</td></tr>"
        html += f"</table><h5>[간접 경험]</h5><ul><li>즐겨 보는 외국 영화/드라마 나라 : {ans.get('ind1','')}</li><li>좋아하는 음악가/연예인 나라 : {ans.get('ind2','')}</li><li>자주 먹는 외국 음식 나라 : {ans.get('ind3','')}</li></ul>"
        html += "<h4>3) 꼭 가 보고 싶은 Top 5 국가와 그 이유</h4><table><tr><th>국가 혹은 지역</th><th>이유</th></tr>"
        for row in ans.get("top5_want", []): 
            html += f"<tr><td>{row.get('국가 혹은 지역','')}</td><td>{row.get('이유','')}</td></tr>"
        html += "</table><h4>4) 절대 가고 싫은 Top 5 국가와 그 이유</h4><table><tr><th>국가 혹은 지역</th><th>이유</th></tr>"
        for row in ans.get("top5_notwant", []): 
            html += f"<tr><td>{row.get('국가 혹은 지역','')}</td><td>{row.get('이유','')}</td></tr>"
        html += "</table><h3>2. 특정 대륙/국가에 대한 자신의 편견과 고정관념</h3><h4>1) 국가별 한 단어 라벨링</h4><table><tr><th>가 보고 싶은 국가</th><th>한 단어 라벨</th><th>가고 싶지 않은 국가</th><th>한 단어 라벨(부정)</th></tr>"
        for row in ans.get("label_df", []): 
            html += f"<tr><td>{row.get('가 보고 싶은 국가','')}</td><td>{row.get('한 단어 라벨','')}</td><td>{row.get('가고 싶지 않은 국가','')}</td><td>{row.get('한 단어 라벨(부정)','')}</td></tr>"
        html += "</table><h4>2) 개인적으로 가장 강한 편견을 가진 국가</h4><table><tr><th>국가명</th><th>편견 내용</th><th>편견 형성 과정 혹은 이유</th></tr>"
        for row in ans.get("prej_df", []): 
            html += f"<tr><td>{row.get('국가명','')}</td><td>{row.get('편견 내용','')}</td><td>{row.get('편견 형성 과정 혹은 이유','')}</td></tr>"
        html += "</table><h4>3) 미디어와 교육의 영향으로 인한 인식 발견</h4><table>"
        html += f"<tr><th>뉴스에서 자주 접하는 국가들</th><td>{ans.get('media1_1','')}</td><th>그 나라들에 대한 이미지</th><td>{ans.get('media1_2','')}</td></tr>"
        html += f"<tr><th>영화/드라마에서 자주 접하는 국가들</th><td>{ans.get('media2_1','')}</td><th>그 나라들에 대한 이미지</th><td>{ans.get('media2_2','')}</td></tr>"
        html += f"<tr><th>학교에서 많이 배운 국가들</th><td>{ans.get('media3_1','')}</td><th>그 나라들에 대한 지식</th><td>{ans.get('media3_2','')}</td></tr></table>"
        html += "<h4>4) 부정확한 정보나 과장된 인식 발견 (사실과 다른 내용들)</h4><table><tr><th>국가명</th><th>잘못 알고 있었던 내용</th><th>실제 사실</th></tr>"
        for row in ans.get("fake_df", []): 
            html += f"<tr><td>{row.get('국가명','')}</td><td>{row.get('잘못 알고 있었던 내용','')}</td><td>{row.get('실제 사실','')}</td></tr>"
        html += "</table><h4>5) 우월감이나 차별 의식 점검</h4><table><tr><th>어떤 국가에 대해?</th><th>어떤 측면에서</th><th>그 이유</th></tr>"
        for row in ans.get("discrim_df", []): 
            html += f"<tr><td>{row.get('어떤 국가에 대해?','')}</td><td>{row.get('어떤 측면에서','')}</td><td>{row.get('그 이유','')}</td></tr>"
        html += "</table><h3>3. 포용적이고 균형잡힌 세계관을 위한 노력</h3><h4>1) 편견을 바꾸고 싶은 국가</h4><table><tr><th>어떤 국가에 대해?</th><th>현재의 편견</th><th>올바른 정보를 찾기 위한 계획</th></tr>"
        for row in ans.get("change_df", []): 
            html += f"<tr><td>{row.get('어떤 국가에 대해?','')}</td><td>{row.get('현재의 편견','')}</td><td>{row.get('올바른 정보를 찾기 위한 계획','')}</td></tr>"
        html += "</table><h4>2) 가장 무관심했던 대륙 혹은 국가</h4><table><tr><th>선택 대륙/국가</th><th>무관심 이유</th><th>관심 확장을 위한 정보 수집 방법</th></tr>"
        for row in ans.get("ignore_df", []): 
            html += f"<tr><td>{row.get('선택 대륙/국가','')}</td><td>{row.get('무관심 이유','')}</td><td>{row.get('관심 확장을 위한 정보 수집 방법','')}</td></tr>"
        html += "</table><h4>3) 서구 중심적 시각 벗어나기</h4><table><tr><th>현재 가지고 있는 서구 중심적 시각</th><th>개선 방법</th></tr>"
        for row in ans.get("western_df", []): 
            html += f"<tr><td>{row.get('현재 가지고 있는 서구 중심적 시각','')}</td><td>{row.get('개선 방법','')}</td></tr>"
        html += "</table><h3>4. 목표로 하는 세계관</h3>"
        html += f"<p><b>▶ 어떤 사람이 되고 싶은가?</b></p><div class='content-box'>{ans.get('goal_1','')}</div>"
        html += f"<p><b>▶ 어떤 세계관을 갖고 싶은가?</b></p><div class='content-box'>{ans.get('goal_2','')}</div>"
        
    elif act_name == ACT_2_1:
        html += f"<h4>👥 모둠 구성원</h4><ul><li>1: {ans.get('m1_id','')} {ans.get('m1_name','')}</li><li>2: {ans.get('m2_id','')} {ans.get('m2_name','')}</li><li>3: {ans.get('m3_id','')} {ans.get('m3_name','')}</li><li>4: {ans.get('m4_id','')} {ans.get('m4_name','')}</li></ul>"
        html += "<h3>Step 1. 우리 지역에 대한 '밈' 수집 및 지리 정보 팩트 체크 일지</h3>"
        html += f"<p><b>1. 우리가 선택한 우리 지역의 밈:</b> {ans.get('step1_1','')}</p>"
        html += f"<p><b>2. 이 밈이 대중에게 심어준 우리 지역에 대한 주관적 이미지:</b> {ans.get('step1_2','')}</p>"
        html += f"<p><b>3. 우리 모둠에게 특별한 장소감, 장소성, 도시 정체성을 주는 장소:</b> {ans.get('step1_3','')}</p>"
        html += f"<p><b>4. 그 장소에서 느끼는 감정이나 생각:</b> {ans.get('step1_4','')}</p>"
        html += "<h3>Step 2. 도시 발달 과정과 객관적 지표</h3>"
        html += f"<p><b>1. 우리 모둠이 탐구할 시기:</b> {ans.get('step2_1_period','')}</p>"
        html += f"<p><b>2. 선택한 시기의 핵심 공간:</b> {ans.get('step2_1_space','')}</p>"
        html += f"<p><b>   객관적 특징:</b> {ans.get('step2_1_feat','')}</p>"
        html += f"<p><b>3. 선택한 시기의 객관적 지리 데이터 혹은 지표:</b> {ans.get('step2_3','')}</p>"
        html += "<h3>Step 3. 살기 좋은 울산의 조건: 거주 적합성 진단</h3><table><tr><th>거주 적합성 요인</th><th>만족도 점수</th><th>한 줄 평가</th></tr>"
        for row in ans.get("step3_df", []): 
            html += f"<tr><td>{row.get('거주 적합성 요인','')}</td><td>{row.get('만족도 점수','')}</td><td>{row.get('한 줄 평가','')}</td></tr>"
        html += "</table><h3>Step 4. 우리의 방식으로 해 보는 울산 브랜딩: 정체성 리뉴얼</h3>"
        html += f"<p><b>1. 기존 프레임(대중의 오해):</b> {ans.get('step4_1','')}</p>"
        html += f"<p><b>2. 우리 모둠이 도출한 지리적 본질:</b> {ans.get('step4_2','')}</p>"
        html += f"<p><b>3. 우리 모둠의 반전 광고 슬로건:</b> {ans.get('step4_3','')}</p>"
        html += f"<p><b>4. 우리 모둠이 제안하는 개선 아이디어:</b> {ans.get('step4_4','')}</p>"

    elif act_name == ACT_2_2:
        html += f"<h4>👥 모둠 구성원</h4><ul><li>1: {ans.get('m1_id','')} {ans.get('m1_name','')}</li><li>2: {ans.get('m2_id','')} {ans.get('m2_name','')}</li><li>3: {ans.get('m3_id','')} {ans.get('m3_name','')}</li><li>4: {ans.get('m4_id','')} {ans.get('m4_name','')}</li></ul>"
        html += "<h3>Step 1. 우리 동네 현황 진단</h3>"
        html += f"<p><b>1. 대상 지역:</b> {ans.get('step1_1','')}</p>"
        html += "<h4>2. 15분 생활권 반경 내 필수 서비스 체크리스트</h4><table><tr><th>구분</th><th>필수 서비스 항목</th><th>충분</th><th>부족 or 없음</th></tr>"
        for row in ans.get("step1_2_df", []): 
            html += f"<tr><td>{row.get('구분','')}</td><td>{row.get('필수 서비스 항목','')}</td><td>{row.get('충분','')}</td><td>{row.get('부족 or 없음','')}</td></tr>"
        html += "</table><h4>3. 선택한 지역의 핵심 문제점</h4>"
        html += f"<p><b>문제점 1:</b> {ans.get('step1_3_1','')}</p><p><b>문제점 2:</b> {ans.get('step1_3_2','')}</p><p><b>문제점 3:</b> {ans.get('step1_3_3','')}</p>"
        
        html += "<h3>Step 2. 도시 개조 포인트를 활용한 트레이드오프 설계</h3>"
        html += "<h4>[도시 개조 포인트 (학생 추가 포함)]</h4>"
        html += generate_points_html(ans.get("step2_point_df", []))
        
        html += "<h4>[트레이드오프 설계표]</h4><table><tr><th>순번</th><th>선택 코드</th><th>버릴 공간</th><th>사용 포인트</th><th>공간 재설계 이유 및 기대효과</th></tr>"
        for row in ans.get("step2_df", []): 
            html += f"<tr><td>{row.get('순번','')}</td><td>{row.get('선택 코드','')}</td><td>{row.get('버릴 공간','')}</td><td>{row.get('사용 포인트','')}</td><td>{row.get('공간 재설계 이유 및 기대효과','')}</td></tr>"
        html += "</table>"
        
        html += "<h3>Step 3. N분 도시 공간 개조 자료</h3>"
        b64_before = ans.get("file_before_data", ans.get("img_before", ""))
        name_before = ans.get("file_before_name", "변경전_스케치.png" if ans.get("img_before") else "")
        if b64_before:
            html += f"<h4>변경 전 자료: {name_before}</h4>"
            if name_before.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                html += f"<img src='data:image/png;base64,{b64_before}' style='max-width:100%; border:1px solid #ccc;'/>"
            else:
                html += "<p>(웹 포트폴리오 상에서는 미리보기를 제공하지 않습니다. 원본을 다운로드해주세요.)</p>"
                
        b64_after = ans.get("file_after_data", ans.get("img_after", ""))
        name_after = ans.get("file_after_name", "변경후_스케치.png" if ans.get("img_after") else "")
        if b64_after:
            html += f"<h4>변경 후 자료: {name_after}</h4>"
            if name_after.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                html += f"<img src='data:image/png;base64,{b64_after}' style='max-width:100%; border:1px solid #ccc;'/>"
            else:
                html += "<p>(웹 포트폴리오 상에서는 미리보기를 제공하지 않습니다. 원본을 다운로드해주세요.)</p>"
                
        html += "<h3>Step 4. 3분 공청회 발표를 위한 준비</h3>"
        html += f"<p><b>1. 핵심 정책 슬로건:</b> {ans.get('step4_1','')}</p>"
        html += f"<p><b>2. 심각한 공간 문제:</b> {ans.get('step4_2','')}</p>"
        html += f"<p><b>3. 버리고 채운 것과 이유:</b> {ans.get('step4_3','')}</p>"
        html += f"<p><b>4. 일상의 변화:</b> {ans.get('step4_4','')}</p>"

    elif act_name == ACT_2_3:
        html += f"<h4>👤 개별 정보</h4><ul><li>학번: {ans.get('ind_id','')}</li><li>이름: {ans.get('ind_name','')}</li><li>희망 진로 혹은 계열: {ans.get('ind_career','')}</li></ul>"
        
        html += """<div style="border: 2px solid #ccc; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h4 style="margin-top: 0; color: #2980b9;">들어가기 — 교과서 20쪽 「진로 탐색: 빛으로 작품을 만드는 미디어 파사드 디자이너」</h4>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <th style="border: 1px solid #ccc; padding: 8px; background-color: #f8f9fa; width: 20%;">어떤 일을 하나요?</th>
                    <td style="border: 1px solid #ccc; padding: 8px;">· 건물의 형태와 주변 환경을 고려하여 특정 주제나 전달하고자 하는 메시지에 맞춘 디자인을 구상한다.<br>· 건축물의 외벽에 프로젝션, LED 스크린, 조명 등 다양한 기술을 활용하여 작품을 구현한다.<br>· 도시 환경에서 새로운 예술적 표현을 창조하고, 공공 공간을 더욱 매력적으로 변화시키는 역할을 한다.</td>
                </tr>
                <tr>
                    <th style="border: 1px solid #ccc; padding: 8px; background-color: #f8f9fa;">무엇을 잘해야 하나요?</th>
                    <td style="border: 1px solid #ccc; padding: 8px;">· 건축물의 외관을 예술적으로 재해석하고, 특정 주제나 메시지를 효과적으로 전달할 수 있는 능력<br>· 건축가, 기술자 등 다양한 분야의 전문가들과 협업할 수 있는 능력</td>
                </tr>
                <tr>
                    <th style="border: 1px solid #ccc; padding: 8px; background-color: #f8f9fa;">이 활동의 핵심 질문</th>
                    <td style="border: 1px solid #ccc; padding: 8px; font-weight: bold;">"내가 미디어 파사드 디자이너라면, 우리 지역을 홍보하기 위해 어떤 작품을 만들 수 있을까?"<br><span style="font-weight: normal;">→ 예쁜 영상을 만드는 활동이 아니다. 우리 지역의 정체성을 근거 있게 찾아내고, 실제 건물이 놓인 조건 안에서 실현 가능한 작품을 설계하는 활동이다.</span></td>
                </tr>
            </table>
        </div>"""

        html += "<h3>Step 1. 우리 지역 정체성 자원 발굴 및 팩트 체크</h3>"
        html += "<table><tr><th>구분</th><th>내가 찾은 정체성 키워드 혹은 문장</th><th>근거가 되는 사실·통계·사건</th><th>출처(기관명/자료명/연도)</th></tr>"
        for row in ans.get("step1_df", []): 
            html += f"<tr><td>{row.get('구분','')}</td><td>{row.get('내가 찾은 정체성 키워드 혹은 문장','')}</td><td>{row.get('근거가 되는 사실·통계·사건','')}</td><td>{row.get('출처(기관명/자료명/연도)','')}</td></tr>"
        html += "</table>"
        html += f"<p><b>▶ 내가 최종 선택한 핵심 키워드 혹은 문장:</b> {ans.get('step1_keyword','')}</p>"
        html += f"<p><b>▶ 내 작품이 전할 단 하나의 메시지:</b> {ans.get('step1_message','')}</p>"

        html += "<h3>Step 2. 캔버스 선정</h3>"
        html += "<table><tr><th>후보(건물명)</th><th>벽면 조건</th><th>관람 조건</th><th>접근성</th><th>예상 제약</th><th>지역 정체성 연관성</th><th>적합도</th></tr>"
        for row in ans.get("step2_df", []): 
            html += f"<tr><td>{row.get('건물명','')}</td><td>{row.get('벽면 조건','')}</td><td>{row.get('관람 조건','')}</td><td>{row.get('접근성','')}</td><td>{row.get('예상 제약','')}</td><td>{row.get('정체성 연관성','')}</td><td>{row.get('적합도(별점)','')}</td></tr>"
        html += "</table>"
        html += f"<p><b>▶ 최종 선정 건물:</b> {ans.get('step2_final_building','')}</p>"
        html += f"<p><b>▶ 선정 이유:</b> {ans.get('step2_reason','')}</p>"

        html += "<h3>Step 3. 주어진 조건 진단 및 대응 설계</h3>"
        html += "<table><tr><th>조건 영역</th><th>현장의 실제 조건</th><th>작품에 미치는 영향</th><th>나의 대응 방안</th></tr>"
        for row in ans.get("step3_df", []): 
            html += f"<tr><td>{row.get('조건 영역','')}</td><td>{row.get('현장의 실제 조건 (확인한 사실)','')}</td><td>{row.get('작품에 미치는 영향','')}</td><td>{row.get('나의 대응 방안','')}</td></tr>"
        html += "</table>"

        html += "<h3>Step 4. 작품 스토리보드 4컷</h3>"
        for i in range(1, 5):
            c_desc = ans.get(f'cut_{i}_desc', '')
            c_name = ans.get(f'cut_{i}_file_name', '')
            html += f"<h4>컷 {i}</h4>"
            html += f"<p><b>장면 설명:</b> {c_desc}</p>"
            b64_file = ans.get(f"cut_{i}_file_data", "")
            if b64_file:
                html += f"<p>첨부된 파일: {c_name}</p>"
                if str(c_name).lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    html += f"<img src='data:image/png;base64,{b64_file}' style='max-width:100%; border:1px solid #ccc;'/>"
        
        html += "<h3>Step 5. 작품 설명 카드 작성 및 갤러리 워크</h3>"
        html += f"<p><b>작품 제목:</b> {ans.get('step5_title','')}</p>"
        html += f"<p><b>전시 장소:</b> {ans.get('step5_place','')}</p>"
        html += f"<p><b>작품 개요:</b> {ans.get('step5_summary','')}</p>"
        html += f"<p><b>지역 정체성 반영:</b> {ans.get('step5_identity','')}</p>"
        html += f"<p><b>현장 조건 반영:</b> {ans.get('step5_condition','')}</p>"
        html += f"<p><b>우리 지역에 남길 변화:</b> {ans.get('step5_change','')}</p>"

        html += "<h3>Step 6. 제출 전 자기 점검 및 활용 기록</h3>"
        html += "<h4>점검 항목 체크리스트</h4><table><tr><th>No</th><th>점검 항목</th><th>확인 여부</th></tr>"
        for row in ans.get("step6_chk_df", []): 
            html += f"<tr><td>{row.get('No','')}</td><td>{row.get('점검 항목','')}</td><td>{'✅' if row.get('확인') else '❌'}</td></tr>"
        html += "</table>"
        html += "<h4>생성형 AI 활용 기록</h4><table><tr><th>사용한 도구명</th><th>입력한 프롬프트</th><th>AI 결과물을 내가 수정·판단한 내용</th></tr>"
        for row in ans.get("step6_ai_df", []): 
            html += f"<tr><td>{row.get('사용한 도구명','')}</td><td>{row.get('입력한 프롬프트','')}</td><td>{row.get('AI 결과물을 내가 수정·판단한 내용','')}</td></tr>"
        html += "</table>"
        html += f"<p><b>▶ 활동 성찰:</b> {ans.get('step6_reflection','')}</p>"
        
    else:
        c_form = config.get("custom_forms", {}).get(act_name, [])
        for q in c_form:
            html += f"<h3>{q['label']}</h3><div class='content-box'>{ans.get(q['id'], '')}</div>"
            
    return html

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

# --- [4] 활동지 렌더링 함수들 ---
def render_activity1_3th(user_key, u_info, current_role):
    category = ACT_3_1
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    
    if current_role == "관리자": 
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. 테스트 저장됩니다.")
        
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    
    if current_role == "학생":
        if disabled_flag: st.error(status_msg, icon="🚫")
        else: st.success(status_msg, icon="✅")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>1. 자신이 선택한 영상에 대한 첫번째 질문</h3>", unsafe_allow_html=True)
    a1_1 = st.text_input("1. 영상의 제목", value=ans.get("a1_1", ""), disabled=disabled_flag, key=f"a1_1_{category}")
    a1_2 = st.text_input("2. 영상에 등장하는 국가 혹은 지역", value=ans.get("a1_2", ""), disabled=disabled_flag, key=f"a1_2_{category}")
    a1_3 = st.text_area("3. 해당 영상을 선택하게 된 이유", value=ans.get("a1_3", ""), disabled=disabled_flag, key=f"a1_3_{category}")
    st.markdown("---")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>2. 자신이 선택한 영상에 대한 두 번째 질문</h3>", unsafe_allow_html=True)
    a2_1 = st.text_area("1. 첫 느낌", value=ans.get("a2_1", ""), disabled=disabled_flag, key=f"a2_1_{category}")
    a2_2_1 = st.text_input("▶ 인상적이었던 장소 혹은 공간:", value=ans.get("a2_2_1", ""), disabled=disabled_flag, key=f"a2_2_1_{category}")
    a2_2_2 = st.text_area("▶ 이유:", value=ans.get("a2_2_2", ""), disabled=disabled_flag, key=f"a2_2_2_{category}")
    a2_3_1 = st.text_input("▶ 누구에게 추천:", value=ans.get("a2_3_1", ""), disabled=disabled_flag, key=f"a2_3_1_{category}")
    a2_3_2 = st.text_area("▶ 추천하는 이유:", value=ans.get("a2_3_2", ""), disabled=disabled_flag, key=f"a2_3_2_{category}")
    a2_4 = st.text_area("4. 영상에 대한 나만의 감상평", value=ans.get("a2_4", ""), disabled=disabled_flag, key=f"a2_4_{category}")
    st.markdown("---")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>5. 만일 내가 영상 속 지역을 배경으로 영상을 찍는다면?</h3>", unsafe_allow_html=True)
    a3_1 = st.text_input("1) 영상의 제목:", value=ans.get("a3_1", ""), disabled=disabled_flag, key=f"a3_1_{category}")
    a3_2 = st.text_input("2) 영상의 주요 컨셉 혹은 느낌:", value=ans.get("a3_2", ""), disabled=disabled_flag, key=f"a3_2_{category}")
    a3_3 = st.text_input("3) 누구와 함께 가고 싶은가?:", value=ans.get("a3_3", ""), disabled=disabled_flag, key=f"a3_3_{category}")
    a3_4 = st.text_area("4) 그 이유는?:", value=ans.get("a3_4", ""), disabled=disabled_flag, key=f"a3_4_{category}")
    a3_5 = st.text_input("5) 그곳에서 가장 해 보고 싶은 것:", value=ans.get("a3_5", ""), disabled=disabled_flag, key=f"a3_5_{category}")
    a3_6 = st.text_area("6) 그 이유는?:", value=ans.get("a3_6", ""), key=f"a3_6_{category}", disabled=disabled_flag)
    a3_7 = st.text_input("7) 영상에 꼭 넣고 싶은 장소 혹은 공간:", value=ans.get("a3_7", ""), disabled=disabled_flag, key=f"a3_7_{category}")
    a3_8 = st.text_area("8) 그 이유는?:", value=ans.get("a3_8", ""), key=f"a3_8_{category}", disabled=disabled_flag)
    a3_9 = st.text_area("9) 만일 내가 썸네일 영상을 만든다면?:", value=ans.get("a3_9", ""), disabled=disabled_flag, key=f"a3_9_{category}")
    a3_10 = st.text_input("10) 어울리는 BGM:", value=ans.get("a3_10", ""), disabled=disabled_flag, key=f"a3_10_{category}")
    a3_11 = st.text_area("11) 그 이유는?:", value=ans.get("a3_11", ""), key=f"a3_11_{category}", disabled=disabled_flag)
    
    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{category}"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "a1_1": a1_1, "a1_2": a1_2, "a1_3": a1_3, "a2_1": a2_1, "a2_2_1": a2_2_1, 
                "a2_2_2": a2_2_2, "a2_3_1": a2_3_1, "a2_3_2": a2_3_2, "a2_4": a2_4, "a3_1": a3_1, 
                "a3_2": a3_2, "a3_3": a3_3, "a3_4": a3_4, "a3_5": a3_5, "a3_6": a3_6, 
                "a3_7": a3_7, "a3_8": a3_8, "a3_9": a3_9, "a3_10": a3_10, "a3_11": a3_11
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data)
            create_auto_backup(f"[{u_name}] {category} 활동 저장")
            st.balloons()
            st.success("🎉 화면 저장이 완료되었습니다!")

def render_activity2_3th(user_key, u_info, current_role):
    category = ACT_3_2
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    
    if current_role == "관리자": 
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. 테스트 저장됩니다.")
        
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    
    if current_role == "학생":
        if disabled_flag: st.error(status_msg, icon="🚫")
        else: st.success(status_msg, icon="✅")
        
    q1_1 = st.text_input("1-1) 나에게 편안함을 주는 장소(공간)이/가 있는가?", value=ans.get("q1_1", ""), disabled=disabled_flag, key=f"q1_1_{category}")
    q1_2 = st.text_area("1-2) 그 장소(공간)이/가 어떤 면에서 나에게 편안함을 주는 것 같은가?", value=ans.get("q1_2", ""), disabled=disabled_flag, key=f"q1_2_{category}")
    q2_1 = st.text_input("2-1) 자신이 생각하기에 자신의 성격은?", value=ans.get("q2_1", ""), disabled=disabled_flag, key=f"q2_1_{category}")
    q2_2 = st.text_input("2-2) 자신이 성격 형성에 있어 영향을 준 장소(공간)이/가 있는가?", value=ans.get("q2_2", ""), disabled=disabled_flag, key=f"q2_2_{category}")
    q2_3 = st.text_area("2-3) 만약 그런 장소(공간)이/가 있다면 무엇 때문인가?", value=ans.get("q2_3", ""), disabled=disabled_flag, key=f"q2_3_{category}")
    q3_1 = st.text_input("3-1) 자신이 생각하기에 자신의 장점은?", value=ans.get("q3_1", ""), disabled=disabled_flag, key=f"q3_1_{category}")
    q3_2 = st.text_input("3-2) 자신이 장점 형성에 있어 영향을 준 장소(공간)이/가 있는가?", value=ans.get("q3_2", ""), disabled=disabled_flag, key=f"q3_2_{category}")
    
    # 🌟 q3_3 Key 중복 오류 방지 적용 완료
    q3_3 = st.text_area("3-3) 만약 그런 장소(공간)이/가 있다면 무엇 때문인가?", value=ans.get("q3_3", ""), disabled=disabled_flag, key=f"q3_3_{category}") 
    
    q4_1 = st.text_input("4-1) 내가 성장함에 있어 영향을 준 장소(공간)이/가 있는가?", value=ans.get("q4_1", ""), disabled=disabled_flag, key=f"q4_1_{category}")
    q4_2 = st.text_area("4-2) 그런 장소(공간)이/가 있다면 어떤 면에서 영향을 준 것 같은가?", value=ans.get("q4_2", ""), disabled=disabled_flag, key=f"q4_2_{category}")
    q5_1 = st.text_input("5-1) 지금 나의 목표는 무엇인가?", value=ans.get("q5_1", ""), disabled=disabled_flag, key=f"q5_1_{category}")
    q5_2 = st.text_area("5-2) 그런 목표를 설정함에 있어 영향을 준 장소(공간)이/가 있는가?", value=ans.get("q5_2", ""), disabled=disabled_flag, key=f"q5_2_{category}")
    q6_1 = st.text_input("6-1) 훗날 소중한 사람에게 소개해 주고 싶은 장소(공간)이/가 있는가?", value=ans.get("q6_1", ""), disabled=disabled_flag, key=f"q6_1_{category}")
    q6_2 = st.text_area("6-2) 만약 그런 장소(공간)이/가 있다면 무엇 때문인가?", value=ans.get("q6_2", ""), disabled=disabled_flag, key=f"q6_2_{category}")
    q7_1 = st.text_input("7-1) 나만의 비밀 장소(공간)이/가 있는가?", value=ans.get("q7_1", ""), disabled=disabled_flag, key=f"q7_1_{category}")
    q7_2 = st.text_area("7-2) 만약 그런 장소(공간)이/가 있다면 무엇 때문인가?", value=ans.get("q7_2", ""), disabled=disabled_flag, key=f"q7_2_{category}")
    q8_1 = st.text_input("8-1) 시간을 돌려 과거로 돌아갈 수 있다면 다시 가 보고 싶은 장소(공간)이/가 있는가?", value=ans.get("q8_1", ""), disabled=disabled_flag, key=f"q8_1_{category}")
    q8_2 = st.text_area("8-2) 장소(공간)로/으로 다시 가 보고 싶은 이유는 무엇 때문인가?", value=ans.get("q8_2", ""), disabled=disabled_flag, key=f"q8_2_{category}")
    
    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{category}"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "q1_1": q1_1, "q1_2": q1_2, "q2_1": q2_1, "q2_2": q2_2, "q2_3": q2_3, "q3_1": q3_1, 
                "q3_2": q3_2, "q3_3": q3_3, "q4_1": q4_1, "q4_2": q4_2, "q5_1": q5_1, "q5_2": q5_2, 
                "q6_1": q6_1, "q6_2": q6_2, "q7_1": q7_1, "q7_2": q7_2, "q8_1": q8_1, "q8_2": q8_2
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data)
            create_auto_backup(f"[{u_name}] {category} 활동 저장")
            st.balloons()
            st.success("🎉 화면 저장이 완료되었습니다!")

def render_activity3_3th(user_key, u_info, current_role):
    category = ACT_3_3
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    
    if current_role == "관리자": 
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. 테스트 저장됩니다.")
        
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    
    if current_role == "학생":
        if disabled_flag: st.error(status_msg, icon="🚫")
        else: st.success(status_msg, icon="✅")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>1. 세계 인식 수준에 대한 확인</h3>", unsafe_allow_html=True)
    continents = ["아시아", "유럽", "북아메리카", "남아메리카", "아프리카", "오세아니아"]
    levels = ["선택", "매우높음", "높음", "보통", "낮음", "매우낮음"]
    s1_dis = True if disabled_flag else ["대륙"]
    s1_df = pd.DataFrame(ans.get("s1_df", [{"대륙": c, "관심도": "선택", "지식수준": "선택"} for c in continents]))
    edited_s1_df = st.data_editor(s1_df, column_config={"관심도": st.column_config.SelectboxColumn("관심도", options=levels, required=True), "지식수준": st.column_config.SelectboxColumn("지식수준", options=levels, required=True)}, disabled=s1_dis, hide_index=True, use_container_width=True, key=f"s1_df_{category}")

    direct_df = pd.DataFrame(ans.get("direct_df", [{"여행해 본 국가": "", "해당 국가에 대한 구체적인 기억 혹은 인상": ""} for _ in range(3)]))
    edited_direct_df = st.data_editor(direct_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"direct_df_{category}")
    
    ind1 = st.text_input("즐겨 보는 외국 영화/드라마는 어느 나라 작품?", value=ans.get("ind1", ""), disabled=disabled_flag, key=f"ind1_{category}")
    ind2 = st.text_input("좋아하는 음악가나 연예인이 있다면 어느 나라?", value=ans.get("ind2", ""), disabled=disabled_flag, key=f"ind2_{category}")
    ind3 = st.text_input("자주 먹는 외국 음식이 있다면 어느 나라?", value=ans.get("ind3", ""), disabled=disabled_flag, key=f"ind3_{category}")
    
    top5_want = pd.DataFrame(ans.get("top5_want", [{"국가 혹은 지역": "", "이유": ""} for _ in range(5)]))
    edited_top5_want = st.data_editor(top5_want, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"top5_want_{category}")
    
    top5_notwant = pd.DataFrame(ans.get("top5_notwant", [{"국가 혹은 지역": "", "이유": ""} for _ in range(5)]))
    edited_top5_notwant = st.data_editor(top5_notwant, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"top5_notwant_{category}")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>2. 특정 대륙/국가에 대한 자신의 편견과 고정관념</h3>", unsafe_allow_html=True)
    label_df = pd.DataFrame(ans.get("label_df", [{"가 보고 싶은 국가": "", "한 단어 라벨": "", "가고 싶지 않은 국가": "", "한 단어 라벨(부정)": ""} for _ in range(3)]))
    edited_label_df = st.data_editor(label_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"label_df_{category}")
    
    prej_df = pd.DataFrame(ans.get("prej_df", [{"국가명": "", "편견 내용": "", "편견 형성 과정 혹은 이유": ""} for _ in range(2)]))
    edited_prej_df = st.data_editor(prej_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"prej_df_{category}")
    
    col1, col2 = st.columns(2)
    media1_1 = col1.text_area("뉴스에서 자주 접하는 국가들", value=ans.get("media1_1", ""), height=80, disabled=disabled_flag, key=f"media1_1_{category}")
    media1_2 = col2.text_area("그 나라들에 대한 이미지 (뉴스)", value=ans.get("media1_2", ""), height=80, disabled=disabled_flag, key=f"media1_2_{category}")
    media2_1 = col1.text_area("영화/드라마에서 자주 접하는 국가들", value=ans.get("media2_1", ""), height=80, disabled=disabled_flag, key=f"media2_1_{category}")
    media2_2 = col2.text_area("그 나라들에 대한 이미지 (영화/드라마)", value=ans.get("media2_2", ""), height=80, disabled=disabled_flag, key=f"media2_2_{category}")
    media3_1 = col1.text_area("학교에서 많이 배운 국가들", value=ans.get("media3_1", ""), height=80, disabled=disabled_flag, key=f"media3_1_{category}")
    media3_2 = col2.text_area("그 나라들에 대한 지식", value=ans.get("media3_2", ""), height=80, disabled=disabled_flag, key=f"media3_2_{category}")
    
    fake_df = pd.DataFrame(ans.get("fake_df", [{"국가명": "", "잘못 알고 있었던 내용": "", "실제 사실": ""} for _ in range(3)]))
    edited_fake_df = st.data_editor(fake_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"fake_df_{category}")
    
    discrim_df = pd.DataFrame(ans.get("discrim_df", [{"어떤 국가에 대해?": "", "어떤 측면에서": "", "그 이유": ""} for _ in range(2)]))
    edited_discrim_df = st.data_editor(discrim_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"discrim_df_{category}")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>3. 포용적이고 균형잡힌 세계관을 위한 노력</h3>", unsafe_allow_html=True)
    change_df = pd.DataFrame(ans.get("change_df", [{"어떤 국가에 대해?": "", "현재의 편견": "", "올바른 정보를 찾기 위한 계획": ""} for _ in range(2)]))
    edited_change_df = st.data_editor(change_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"change_df_{category}")
    
    ignore_df = pd.DataFrame(ans.get("ignore_df", [{"선택 대륙/국가": "", "무관심 이유": "", "관심 확장을 위한 정보 수집 방법": ""} for _ in range(2)]))
    edited_ignore_df = st.data_editor(ignore_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"ignore_df_{category}")
    
    western_df = pd.DataFrame(ans.get("western_df", [{"현재 가지고 있는 서구 중심적 시각": "", "개선 방법": ""} for _ in range(2)]))
    edited_western_df = st.data_editor(western_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"western_df_{category}")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>4. 목표로 하는 세계관</h3>", unsafe_allow_html=True)
    goal_1 = st.text_area("▶ 어떤 사람이 되고 싶은가?", value=ans.get("goal_1", ""), height=100, disabled=disabled_flag, key=f"goal_1_{category}")
    goal_2 = st.text_area("▶ 어떤 세계관을 갖고 싶은가?", value=ans.get("goal_2", ""), height=100, disabled=disabled_flag, key=f"goal_2_{category}")
    
    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{category}"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "s1_df": edited_s1_df.to_dict('records'), "direct_df": edited_direct_df.to_dict('records'),
                "ind1": ind1, "ind2": ind2, "ind3": ind3, "top5_want": edited_top5_want.to_dict('records'),
                "top5_notwant": edited_top5_notwant.to_dict('records'), "label_df": edited_label_df.to_dict('records'),
                "prej_df": edited_prej_df.to_dict('records'), "media1_1": media1_1, "media1_2": media1_2,
                "media2_1": media2_1, "media2_2": media2_2, "media3_1": media3_1, "media3_2": media3_2,
                "fake_df": edited_fake_df.to_dict('records'), "discrim_df": edited_discrim_df.to_dict('records'),
                "change_df": edited_change_df.to_dict('records'), "ignore_df": edited_ignore_df.to_dict('records'),
                "western_df": edited_western_df.to_dict('records'), "goal_1": goal_1, "goal_2": goal_2
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data)
            create_auto_backup(f"[{u_name}] {category} 활동 저장")
            st.balloons()
            st.success("🎉 화면 저장이 완료되었습니다!")

def render_activity1_2nd(user_key, u_info, current_role):
    category = ACT_2_1
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    
    is_member_view = False
    if current_role == "학생" and owner_key != user_key:
        is_member_view = True
        disabled_flag = True
        
    if current_role == "관리자": 
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다.")
        
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    
    if is_member_view: 
        st.info("💡 **[조회 전용]** 모둠장(대표)이 작성 및 저장한 화면을 연동하여 조회 중입니다.")
    elif current_role == "학생":
        if disabled_flag: st.error(status_msg.replace('\n', '<br>'), icon="🚫")
        else: st.success(status_msg, icon="✅")

    m1_id, m1_name, m2_id, m2_name, m3_id, m3_name, m4_id, m4_name = render_group_members(ans, disabled_flag, category)
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 1. 우리 지역에 대한 '밈' 수집 및 지리 정보 팩트 체크 일지</h3>", unsafe_allow_html=True)
    step1_1 = st.text_input("1. 우리가 선택한 우리 지역의 인터넷, SNS, 혹은 타 지역 친구들에게 들었던 우리 지역에 대한 유쾌한 편견이나 밈을 하나 선정 '밈'", value=ans.get("step1_1", ""), disabled=disabled_flag, key=f"step1_1_{category}")
    step1_2 = st.text_input("2. 이 밈이 대중에게 심어준 주관적 이미지 (편견 혹은 선입견)", value=ans.get("step1_2", ""), disabled=disabled_flag, key=f"step1_2_{category}")
    step1_3 = st.text_input("3. 우리 모둠에게 특별한 장소감을 주는 장소", value=ans.get("step1_3", ""), disabled=disabled_flag, key=f"step1_3_{category}")
    step1_4 = st.text_area("4. 그 장소에서 느끼는 감정이나 생각", value=ans.get("step1_4", ""), disabled=disabled_flag, key=f"step1_4_{category}")
    
    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 2. 도시 발달 과정과 객관적 지표</h3>", unsafe_allow_html=True)
    step2_1_period = st.radio("1. 우리 모둠이 탐구할 시기", ["조선시대", "1960~70년대", "1980~90년대", "2000년대~현재"], index=["조선시대", "1960~70년대", "1980~90년대", "2000년대~현재"].index(ans.get("step2_1_period", "조선시대")) if ans.get("step2_1_period") in ["조선시대", "1960~70년대", "1980~90년대", "2000년대~현재"] else 0, disabled=disabled_flag, horizontal=True, key=f"step2_1_period_{category}")
    step2_1_space = st.text_input("2-1. 선택한 시기의 핵심 공간", value=ans.get("step2_1_space", ""), disabled=disabled_flag, key=f"step2_1_space_{category}")
    step2_1_feat = st.text_input("2-2. 객관적 특징", value=ans.get("step2_1_feat", ""), disabled=disabled_flag, key=f"step2_1_feat_{category}")
    step2_3 = st.text_area("3. 선택한 시기의 객관적 지리 데이터 혹은 지표", value=ans.get("step2_3", ""), disabled=disabled_flag, key=f"step2_3_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 3. 살기 좋은 울산의 조건: 거주 적합성 진단</h3>", unsafe_allow_html=True)
    stars = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
    default_step3 = [{"거주 적합성 요인": "경제 성장", "만족도 점수": "⭐⭐⭐⭐", "한 줄 평가": "대한민국 최대의 산업수도답게 일자리와 경제적 활력이 뛰어남"}] + [{"거주 적합성 요인": "", "만족도 점수": "⭐⭐⭐", "한 줄 평가": ""} for _ in range(4)]
    step3_df = pd.DataFrame(ans.get("step3_df", default_step3))
    edited_step3_df = st.data_editor(step3_df, column_config={"만족도 점수": st.column_config.SelectboxColumn("만족도 점수", options=stars, required=True)}, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step3_df_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 4. 우리의 방식으로 해 보는 울산 브랜딩: 정체성 리뉴얼</h3>", unsafe_allow_html=True)
    step4_1 = st.text_input("1. 기존 프레임(대중의 오해)", value=ans.get("step4_1", ""), disabled=disabled_flag, key=f"step4_1_{category}")
    step4_2 = st.text_input("2. 우리 모둠이 도출한 지리적 본질", value=ans.get("step4_2", ""), disabled=disabled_flag, key=f"step4_2_{category}")
    step4_3 = st.text_input("3. 우리 모둠의 반전 광고 슬로건", value=ans.get("step4_3", ""), disabled=disabled_flag, key=f"step4_3_{category}")
    step4_4 = st.text_area("4. 우리 모둠이 제안하는 울산의 거주 적합성 개선 아이디어", value=ans.get("step4_4", ""), disabled=disabled_flag, key=f"step4_4_{category}")

    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{category}"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "m1_id": m1_id, "m1_name": m1_name, "m2_id": m2_id, "m2_name": m2_name, "m3_id": m3_id, "m3_name": m3_name, "m4_id": m4_id, "m4_name": m4_name,
                "step1_1": step1_1, "step1_2": step1_2, "step1_3": step1_3, "step1_4": step1_4,
                "step2_1_period": step2_1_period, "step2_1_space": step2_1_space, "step2_1_feat": step2_1_feat, "step2_3": step2_3,
                "step3_df": edited_step3_df.to_dict('records'), "step4_1": step4_1, "step4_2": step4_2, "step4_3": step4_3, "step4_4": step4_4
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data)
            create_auto_backup(f"[{u_name}] {category} 활동 저장")
            st.balloons()
            st.success("🎉 화면 저장이 완료되었습니다!")

def render_activity2_2nd(user_key, u_info, current_role):
    category = ACT_2_2
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    
    is_member_view = False
    if current_role == "학생" and owner_key != user_key:
        is_member_view = True; disabled_flag = True
        
    if current_role == "관리자": 
        disabled_flag = False; st.info("💡 교사/관리자 모드입니다.")
        
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    if is_member_view: 
        st.info("💡 **[조회 전용]** 모둠장(대표)이 작성 및 저장한 화면을 연동하여 조회 중입니다.")
    elif current_role == "학생":
        if disabled_flag: st.error(status_msg.replace('\n', '<br>'), icon="🚫")
        else: st.success(status_msg, icon="✅")

    m1_id, m1_name, m2_id, m2_name, m3_id, m3_name, m4_id, m4_name = render_group_members(ans, disabled_flag, category)
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 1. 우리 동네 현황 진단</h3>", unsafe_allow_html=True)
    step1_1 = st.text_input("1. 대상 지역 (예: 학교 주변 인근 00아파트 00단지 일대)", value=ans.get("step1_1", ""), disabled=disabled_flag, key=f"step1_1_{category}")
    
    default_step1_2 = [
        {"구분": "주거 및 생활", "필수 서비스 항목": "생필품 마트, 일상 편의시설", "충분": False, "부족 or 없음": False},
        {"구분": "의료 및 돌봄", "필수 서비스 항목": "병원, 약국, 돌봄센터", "충분": False, "부족 or 없음": False},
        {"구분": "노동 및 학습", "필수 서비스 항목": "청소년 무료 학습/스터디 공간", "충분": False, "부족 or 없음": False},
        {"구분": "여가 및 녹지", "필수 서비스 항목": "공원, 수변 공간, 휴식 공간", "충분": False, "부족 or 없음": False},
        {"구분": "교육 및 문화", "필수 서비스 항목": "도서관, 학습 공간, 문화 시설", "충분": False, "부족 or 없음": False},
        {"구분": "이동 및 보행", "필수 서비스 항목": "보행자 전용 도로, 자전거 도로", "충분": False, "부족 or 없음": False}
    ]
    step1_2_df = pd.DataFrame(ans.get("step1_2_df", default_step1_2))
    edited_step1_2_df = st.data_editor(step1_2_df, hide_index=True, use_container_width=True, disabled=disabled_flag, num_rows="dynamic", key=f"step1_2_df_{category}")
    
    step1_3_1 = st.text_area("문제점 1 / 데이터:", value=ans.get("step1_3_1", ""), disabled=disabled_flag, height=80, key=f"step1_3_1_{category}")
    step1_3_2 = st.text_area("문제점 2 / 데이터:", value=ans.get("step1_3_2", ""), disabled=disabled_flag, height=80, key=f"step1_3_2_{category}")
    step1_3_3 = st.text_area("문제점 3 / 데이터:", value=ans.get("step1_3_3", ""), disabled=disabled_flag, height=80, key=f"step1_3_3_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 2. 도시 개조 포인트를 활용한 트레이드오프 설계</h3>", unsafe_allow_html=True)
    
    default_point_table = [
        {"카테고리": "안전한 보행 환경", "코드": "A-1", "세부 개조 항목": "여고생 안심 귀가 스마트 로드 (CCTV 연동)", "비용": "-15pt"},
        {"카테고리": "", "코드": "A-2", "세부 개조 항목": "아파트 단지 간 담장 철거 및 공공 보행로 연결", "비용": "-20pt"},
        {"카테고리": "", "코드": "A-3", "세부 개조 항목": "차로 축소 및 쾌적한 보행을 위한 녹지 공간 조성", "비용": "-20pt"},
        {"카테고리": "", "코드": "A-4", "세부 개조 항목": "스마트 횡단보도 및 교통약자/학생 쉼터", "비용": "-10pt"},
        {"카테고리": "", "코드": "A-5", "세부 개조 항목": "야간 자율학습 후 안전 귀가를 위한 셉테드(CPTED) 조명", "비용": "-10pt"},
        {"카테고리": "", "코드": "A-6", "세부 개조 항목": "", "비용": ""},
        {"카테고리": "녹지 및 생태공간 구축", "코드": "B-1", "세부 개조 항목": "아파트 상가/방치 공터 → 도심 소공원 조성", "비용": "-15pt"},
        {"카테고리": "", "코드": "B-2", "세부 개조 항목": "도심 바람길 숲 및 수변 산책로 조성", "비용": "-15pt"},
        {"카테고리": "", "코드": "B-3", "세부 개조 항목": "에코 펫파크(반려견 전용 공원 및 산책로)", "비용": "-15pt"},
        {"카테고리": "", "코드": "B-4", "세부 개조 항목": "아파트 벽면 녹화 및 옥상 정원(학생 쉼터) 조성", "비용": "-15pt"},
        {"카테고리": "", "코드": "B-5", "세부 개조 항목": "", "비용": ""},
        {"카테고리": "문화와 교육을 위한 공간", "코드": "C-1", "세부 개조 항목": "24시간 공공 스터디 & 커뮤니티 카페", "비용": "-15pt"},
        {"카테고리": "", "코드": "C-2", "세부 개조 항목": "청소년 팝업 스튜디오 & 소공연장 (여고생 동아리 특화)", "비용": "-15pt"},
        {"카테고리": "", "코드": "C-3", "세부 개조 항목": "친환경 스마트 팜", "비용": "-10pt"},
        {"카테고리": "", "코드": "C-4", "세부 개조 항목": "프리미엄 복합 문화 공간 (북카페, 피트니스 존 등)", "비용": "-20pt"},
        {"카테고리": "", "코드": "C-5", "세부 개조 항목": "", "비용": ""},
        {"카테고리": "효율적인 교통과 모빌리티 구축", "코드": "D-1", "세부 개조 항목": "공유 자전거 및 킥보드 전용 도로", "비용": "-15pt"},
        {"카테고리": "", "코드": "D-2", "세부 개조 항목": "스마트 버스 쉘터(공기 청정, 온열 의자 구축)", "비용": "-10pt"},
        {"카테고리": "", "코드": "D-3", "세부 개조 항목": "등하교 혼잡 방지용 아파트 단지 앞 스마트 승하차 존", "비용": "-15pt"},
        {"카테고리": "", "코드": "D-4", "세부 개조 항목": "", "비용": ""}
    ]
    saved_points = ans.get("step2_point_df", default_point_table)
    cat_names = ["안전한 보행 환경", "녹지 및 생태공간 구축", "문화와 교육을 위한 공간", "효율적인 교통과 모빌리티 구축"]
    
    categorized_data = {cat: [] for cat in cat_names}
    current_cat = cat_names[0]
    for row in saved_points:
        cat_val = str(row.get("카테고리", "")).strip()
        if cat_val in cat_names: current_cat = cat_val
        categorized_data[current_cat].append(row)
        
    edited_points_merged = []
    for i, cat in enumerate(cat_names):
        st.markdown(f"<h5 style='color:#2c3e50; margin-top:20px; margin-bottom:5px; font-size:20px !important; font-weight:800;'>🔹 {cat}</h5>", unsafe_allow_html=True)
        df_cat = pd.DataFrame(categorized_data[cat])
        edited_df_cat = st.data_editor(
            df_cat, key=f"editor_cat_{i}_{category}", num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag,
            column_config={"카테고리": None, "코드": st.column_config.TextColumn("코드", required=True, width="small"), "세부 개조 항목": st.column_config.TextColumn("세부 개조 항목", width="large"), "비용": st.column_config.TextColumn("비용", width="small")}
        )
        for j, record in enumerate(edited_df_cat.to_dict('records')):
            record["카테고리"] = cat if j == 0 else ""
            edited_points_merged.append(record)

    st.markdown("#### ▶ 도시 개조 트레이드오프 설계표")
    default_step2 = [{"순번": str(i+1), "선택 코드": "", "버릴 공간": "", "사용 포인트": "", "공간 재설계 이유 및 기대효과": ""} for i in range(8)]
    step2_df = pd.DataFrame(ans.get("step2_df", default_step2))
    edited_step2_df = st.data_editor(step2_df, hide_index=True, use_container_width=True, disabled=disabled_flag, num_rows="dynamic", key=f"step2_df_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 3. N분 도시 공간 개조 자료 스케치/기획안 업로드</h3>", unsafe_allow_html=True)
    
    col_img1, col_img2 = st.columns(2)
    with col_img1:
        st.markdown("**[변경 전 자료]**")
        b64_before = ans.get("file_before_data", ans.get("img_before", ""))
        name_before = ans.get("file_before_name", "변경전_스케치.png" if ans.get("img_before") else "")
        if b64_before:
            st.success(f"📎 등록된 파일: {name_before}")
            if name_before.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                st.image(base64.b64decode(b64_before), use_container_width=True)
            if not disabled_flag and st.button("🗑️ 변경 전 자료 삭제", key=f"del_saved_before_{category}"):
                current_data = load_json(DATA_FILE, {}) 
                if user_key in current_data and category in current_data[user_key]:
                    current_data[user_key][category]["img_before"] = ""
                    current_data[user_key][category]["file_before_data"] = ""
                    current_data[user_key][category]["file_before_name"] = ""
                    save_json(DATA_FILE, current_data); st.rerun()
        file_before = st.file_uploader("변경 전 파일 첨부", key=f"up_before_{category}", disabled=disabled_flag)
        if file_before and not disabled_flag:
            b64_before = base64.b64encode(file_before.getvalue()).decode("utf-8")
            name_before = file_before.name

    with col_img2:
        st.markdown("**[변경 후 자료]**")
        b64_after = ans.get("file_after_data", ans.get("img_after", ""))
        name_after = ans.get("file_after_name", "변경후_스케치.png" if ans.get("img_after") else "")
        if b64_after:
            st.success(f"📎 등록된 파일: {name_after}")
            if name_after.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                st.image(base64.b64decode(b64_after), use_container_width=True)
            if not disabled_flag and st.button("🗑️ 변경 후 자료 삭제", key=f"del_saved_after_{category}"):
                current_data = load_json(DATA_FILE, {}) 
                if user_key in current_data and category in current_data[user_key]:
                    current_data[user_key][category]["img_after"] = ""
                    current_data[user_key][category]["file_after_data"] = ""
                    current_data[user_key][category]["file_after_name"] = ""
                    save_json(DATA_FILE, current_data); st.rerun()
        file_after = st.file_uploader("변경 후 파일 첨부", key=f"up_after_{category}", disabled=disabled_flag)
        if file_after and not disabled_flag:
            b64_after = base64.b64encode(file_after.getvalue()).decode("utf-8")
            name_after = file_after.name

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 4. 3분 공청회 발표를 위한 준비</h3>", unsafe_allow_html=True)
    step4_1 = st.text_input("1. 핵심 정책 슬로건", value=ans.get("step4_1", ""), disabled=disabled_flag, key=f"step4_1_{category}")
    step4_2 = st.text_area("2. 심각한 공간 문제", value=ans.get("step4_2", ""), disabled=disabled_flag, key=f"step4_2_{category}")
    step4_3 = st.text_area("3. 버리고 채운 것과 이유", value=ans.get("step4_3", ""), disabled=disabled_flag, key=f"step4_3_{category}")
    step4_4 = st.text_area("4. 일상의 변화", value=ans.get("step4_4", ""), disabled=disabled_flag, key=f"step4_4_{category}")

    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{category}"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "m1_id": m1_id, "m1_name": m1_name, "m2_id": m2_id, "m2_name": m2_name, "m3_id": m3_id, "m3_name": m3_name, "m4_id": m4_id, "m4_name": m4_name,
                "step1_1": step1_1, "step1_2_df": edited_step1_2_df.to_dict('records'),
                "step1_3_1": step1_3_1, "step1_3_2": step1_3_2, "step1_3_3": step1_3_3,
                "step2_point_df": edited_points_merged, "step2_df": edited_step2_df.to_dict('records'),
                "file_before_data": b64_before, "file_before_name": name_before,
                "file_after_data": b64_after, "file_after_name": name_after,
                "img_before": b64_before, "img_after": b64_after,
                "step4_1": step4_1, "step4_2": step4_2, "step4_3": step4_3, "step4_4": step4_4
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data); ans = new_ans
            create_auto_backup(f"[{u_name}] {category} 활동 저장")
            st.balloons(); st.success("🎉 화면 저장이 완료되었습니다!")

def render_activity3_2nd(user_key, u_info, current_role):
    category = ACT_2_3
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    
    if current_role == "관리자": 
        disabled_flag = False; st.info("💡 교사/관리자 모드입니다.")

    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    if current_role == "학생":
        if disabled_flag: st.error(status_msg.replace('\n', '<br>'), icon="🚫")
        else: st.success(status_msg, icon="✅")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 20px; margin-bottom: 15px;'>👤 개별 정보 입력</h3>", unsafe_allow_html=True)
    col_i1, col_i2, col_i3 = st.columns(3)
    ind_id = col_i1.text_input("학번", value=ans.get("ind_id", u_id), disabled=True if current_role == "학생" else disabled_flag, key=f"ind_id_{category}")
    ind_name = col_i2.text_input("이름", value=ans.get("ind_name", u_name), disabled=True if current_role == "학생" else disabled_flag, key=f"ind_name_{category}")
    ind_career = col_i3.text_input("희망 진로", value=ans.get("ind_career", ""), disabled=disabled_flag, key=f"ind_career_{category}")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 1. 우리 지역 정체성 자원 발굴 및 팩트 체크</h3>", unsafe_allow_html=True)
    default_step1 = [{"구분": "1. 자연/생태", "내가 찾은 정체성 키워드 혹은 문장": "", "근거가 되는 사실·통계·사건": "", "출처(기관명/자료명/연도)": ""}]
    step1_df = pd.DataFrame(ans.get("step1_df", default_step1))
    edited_step1_df = st.data_editor(step1_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step1_df_{category}")
    step1_keyword = st.text_input("▶ 최종 선택 키워드", value=ans.get("step1_keyword", ""), disabled=disabled_flag, key=f"step1_kw_{category}")
    step1_message = st.text_area("▶ 단 하나의 메시지", value=ans.get("step1_message", ""), disabled=disabled_flag, key=f"step1_msg_{category}")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 2. 캔버스 선정</h3>", unsafe_allow_html=True)
    stars = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
    default_step2 = [{"건물명": "후보 1: ", "벽면 조건": "", "관람 조건": "", "접근성": "", "예상 제약": "", "정체성 연관성": "", "적합도(별점)": "⭐⭐⭐"}]
    step2_df = pd.DataFrame(ans.get("step2_df", default_step2))
    edited_step2_df = st.data_editor(step2_df, column_config={"적합도(별점)": st.column_config.SelectboxColumn("적합도(별점)", options=stars, required=True)}, use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step2_df_{category}")
    step2_final_building = st.text_input("▶ 최종 선정 건물", value=ans.get("step2_final_building", ""), disabled=disabled_flag, key=f"step2_final_{category}")
    step2_reason = st.text_area("▶ 이유", value=ans.get("step2_reason", ""), disabled=disabled_flag, key=f"step2_rsn_{category}")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 3. 주어진 조건 진단 및 대응 설계</h3>", unsafe_allow_html=True)
    default_step3 = [{"조건 영역": "1. 물리적 조건", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""}]
    step3_df = pd.DataFrame(ans.get("step3_df", default_step3))
    edited_step3_df = st.data_editor(step3_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step3_df_{category}")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 4. 작품 스토리보드 4컷</h3>", unsafe_allow_html=True)
    new_cuts = {}
    for i in range(1, 5):
        st.markdown(f"**[ 컷 {i} ]**")
        col_c1, col_c2 = st.columns([1, 1])
        with col_c1:
            b64_file = ans.get(f"cut_{i}_file_data", "")
            file_name = ans.get(f"cut_{i}_file_name", "")
            if b64_file:
                st.success(f"📎 등록된 파일: {file_name}")
                if file_name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')): 
                    st.image(base64.b64decode(b64_file), use_container_width=True)
                if not disabled_flag and st.button(f"🗑️ 컷 {i} 자료 삭제", key=f"del_cut_{i}_{category}"):
                    current_data = load_json(DATA_FILE, {}) 
                    if user_key in current_data and category in current_data[user_key]:
                        current_data[user_key][category][f"cut_{i}_file_data"] = ""
                        current_data[user_key][category][f"cut_{i}_file_name"] = ""
                        save_json(DATA_FILE, current_data); st.rerun()
            file_cut = st.file_uploader(f"컷 {i} 파일 첨부", key=f"up_cut_{i}_{category}", disabled=disabled_flag)
            if file_cut and not disabled_flag:
                b64_file = base64.b64encode(file_cut.getvalue()).decode("utf-8")
                file_name = file_cut.name
            new_cuts[f"cut_{i}_file_data"] = b64_file
            new_cuts[f"cut_{i}_file_name"] = file_name
        with col_c2:
            new_cuts[f"cut_{i}_desc"] = st.text_area(f"컷 {i} 설명", value=ans.get(f"cut_{i}_desc", ""), height=100, disabled=disabled_flag, key=f"desc_cut_{i}_{category}")
        st.write("---")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 5. 작품 설명 카드 작성</h3>", unsafe_allow_html=True)
    step5_title = st.text_input("▶ 작품 제목", value=ans.get("step5_title", ""), disabled=disabled_flag, key=f"step5_title_{category}")
    step5_place = st.text_input("▶ 전시 장소", value=ans.get("step5_place", ""), disabled=disabled_flag, key=f"step5_place_{category}")
    step5_summary = st.text_area("▶ 작품 개요", value=ans.get("step5_summary", ""), disabled=disabled_flag, key=f"step5_sum_{category}")
    step5_identity = st.text_area("▶ 지역 정체성 반영", value=ans.get("step5_identity", ""), disabled=disabled_flag, key=f"step5_id_{category}")
    step5_condition = st.text_area("▶ 현장 조건 반영", value=ans.get("step5_condition", ""), disabled=disabled_flag, key=f"step5_cond_{category}")
    step5_change = st.text_area("▶ 남길 변화", value=ans.get("step5_change", ""), disabled=disabled_flag, key=f"step5_chg_{category}")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 6. 제출 전 자기 점검 및 활용 기록</h3>", unsafe_allow_html=True)
    checklist_items = ["Step 1 출처 적음", "실제 답사/확인 완료", "대응 방안 작성 완료"]
    default_checklist = [{"No": i+1, "점검 항목": item, "확인": False} for i, item in enumerate(checklist_items)]
    step6_chk_df = pd.DataFrame(ans.get("step6_chk_df", default_checklist))
    edited_step6_chk_df = st.data_editor(step6_chk_df, hide_index=True, use_container_width=True, disabled=disabled_flag, key=f"step6_chk_{category}")
    
    default_ai = [{"사용한 도구명": "", "입력한 프롬프트": "", "AI 결과물을 내가 수정·판단한 내용": ""}]
    step6_ai_df = pd.DataFrame(ans.get("step6_ai_df", default_ai))
    edited_step6_ai_df = st.data_editor(step6_ai_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step6_ai_{category}")
    step6_reflection = st.text_area("▶ 활동 성찰", value=ans.get("step6_reflection", ""), height=120, disabled=disabled_flag, key=f"step6_ref_{category}")
    
    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{category}"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "ind_id": ind_id, "ind_name": ind_name, "ind_career": ind_career,
                "step1_df": edited_step1_df.to_dict('records'), "step1_keyword": step1_keyword, "step1_message": step1_message,
                "step2_df": edited_step2_df.to_dict('records'), "step2_final_building": step2_final_building, "step2_reason": step2_reason,
                "step3_df": edited_step3_df.to_dict('records'), "step5_title": step5_title, "step5_place": step5_place,
                "step5_summary": step5_summary, "step5_identity": step5_identity, "step5_condition": step5_condition, "step5_change": step5_change,
                "step6_chk_df": edited_step6_chk_df.to_dict('records'), "step6_ai_df": edited_step6_ai_df.to_dict('records'), "step6_reflection": step6_reflection
            }
            new_ans.update(new_cuts)
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data); ans = new_ans
            create_auto_backup(f"[{u_name}] {category} 활동 저장")
            st.balloons(); st.success("🎉 화면 저장이 완료되었습니다!")

def render_custom_activity(user_key, u_info, current_role, act_name, config):
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, act_name, learning_data)
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
            
    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{act_name}"):
            current_data = load_json(DATA_FILE, {})
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][act_name] = new_ans
            save_json(DATA_FILE, current_data); ans = new_ans
            create_auto_backup(f"[{u_name}] {act_name} 저장")
            st.balloons(); st.success("🎉 화면 저장이 완료되었습니다!")
            
    if ans:
        html_data = f"<!DOCTYPE html><html lang='ko'><head><meta charset='UTF-8'><title>{u_name}</title></head><body><h2>▶ {act_name}</h2>"
        for q in custom_form: html_data += f"<h3>{q['label']}</h3><p>{ans.get(q['id'],'')}</p>"
        html_data += "</body></html>"
        st.download_button("📥 내 작성 내용 다운로드 (웹문서)", data=html_data.encode('utf-8-sig'), file_name=f"{u_name}_{act_name}.html", mime="text/html")

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

# 🌟 세션 초기화 블록 복원 (AttributeError 완벽 해결)
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
st.sidebar.markdown("<div style='text-align: center; color: #222; font-size: 18px; font-weight: 900;'>Made by<br><span style='font-size: 24px; color: #000;'>신선여자고등학교 김명남</span></div>", unsafe_allow_html=True)
st.sidebar.markdown("<br>", unsafe_allow_html=True)

with st.sidebar:
    render_privacy_policy()

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
                    act_html = generate_activity_html(act, ans, u_info['name'])
                    st.download_button(label=f"📥 [{act}] 결과물 다운로드", data=act_html.encode('utf-8-sig'), file_name=f"{u_info['name']}_{act}.html", mime="text/html", key=f"stu_dl_{act}")
            if not has_individual_dl: 
                st.info("아직 제출한 활동지가 없습니다.")
            st.markdown("---")
            html_content_all = generate_portfolio_html(current_user_key, u_info, u_info['subject'], app_config, learning_data)
            st.download_button(label=f"📦 {u_info['name']} 학생 전체 포트폴리오 일괄 다운로드 (웹문서)", data=html_content_all.encode('utf-8-sig'), file_name=f"{u_info['name']}_전체_포트폴리오.html", mime="text/html", type="primary")

        elif current_role == "관리자":
            st.markdown("<h1 style='font-size:36px; font-weight:900; color:#000;'>🛠️ 관리자(교사) 대시보드</h1>", unsafe_allow_html=True)
            menu_tabs = st.tabs(["📌 메인 화면/기한 설정", "🗂️ 수행평가 문항 제작", "👥 회원 관리", "📥 학생 제출 자료 조회 및 관리", "💾 DB 수동 백업 및 복구", "🛡️ 자동 백업 센터"])
            
            with menu_tabs[0]:
                if st.session_state.get("admin_save_success", False):
                    st.balloons()
                    st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 화면 저장이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>변경하신 내용이 안전하게 저장되어 즉시 반영됩니다.</p></div>", unsafe_allow_html=True)
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
                else: 
                    st.info("🔒 반별 공개 설정은 왼쪽 사이드바에서 특정 과목을 선택해주세요.")

                st.markdown("---")
                # 🌟 [요구기능 추가] 특정 학생 개별 기한 연장 및 예외 권한 부여
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
                            opt_stu = ["선택"] + list(filt_stu.keys())
                            sel_stu = st.selectbox("학생 선택", opt_stu, format_func=lambda x: "선택" if x == "선택" else f"[{filt_stu[x].get('class_group')}] {filt_stu[x].get('name')} ({filt_stu[x].get('id')})")
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
                        if t_title or t_content: 
                            new_notices.append({"id": f"not_{datetime.datetime.now().strftime('%d%H%M%S')}_{len(new_notices)}", "subject": admin_view_subj, "target_class": t_cls, "제목": t_title, "내용": t_content})
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
                                    c_slots = c_data.get("slots", [{"day": "선택안함", "period": "선택안함", "start": "00:00", "end": "00:00"}] * 3)
                                    while len(c_slots) < 3: c_slots.append({"day": "선택안함", "period": "선택안함", "start": "00:00", "end": "00:00"})
                                    updated_slots = []
                                    for i in range(3):
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
                
                # 🌟 [요청 기능 복구] 승인 완료 학생 목록 표시
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

                # 🌟 [요청 기능 추가] 학생 정보 수정 및 계정 영구 삭제 기능 통합
                st.markdown("### 📝 학생 회원 정보 수정 및 계정 삭제")
                search_edit = st.text_input("🔍 검색 (이름 또는 학번 입력)", key="search_edit")
                filtered_for_edit = filter_students_admin(all_users, search_edit, approved_only=False)
                options_edit = ["선택"] + list(filtered_for_edit.keys())
                edit_target = st.selectbox("학생 선택", options_edit, index=1 if (search_edit.strip() and len(filtered_for_edit) > 0) else 0, format_func=lambda x: "선택" if x=="선택" else f"[{filtered_for_edit[x].get('class_group')}] {filtered_for_edit[x].get('name')} ({filtered_for_edit[x].get('id')})")
                
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
                        
                        if btn_edit:
                            fresh_users = load_json(USERS_FILE, {})
                            fresh_data = load_json(DATA_FILE, {})
                            new_key = f"{e_subj}_{e_cls}_{e_id}"
                            new_info = target_info.copy()
                            new_info.update({"subject": e_subj, "class_group": e_cls, "id": e_id, "name": e_name, "password": e_pw})
                            if new_key != edit_target:
                                fresh_users[new_key] = new_info
                                del fresh_users[edit_target]
                                if edit_target in fresh_data:
                                    fresh_data[new_key] = fresh_data[edit_target]
                                    del fresh_data[edit_target]
                                    save_json(DATA_FILE, fresh_data)
                                save_json(USERS_FILE, fresh_users)
                            else:
                                fresh_users[edit_target] = new_info
                                save_json(USERS_FILE, fresh_users)
                            create_auto_backup(f"[{e_name}] 정보 수정")
                            st.success("수정 완료"); st.rerun()
                            
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
                            st.success("해당 계정과 데이터가 성공적으로 완전 삭제되었습니다."); st.rerun()

            with menu_tabs[3]:
                col_t, col_b = st.columns([8, 2])
                with col_t: st.markdown("### 📥 학생 학습 활동 및 제출 자료 실시간 조회")
                with col_b:
                    if st.button("🔄 실시간 새로고침", type="primary", use_container_width=True): st.rerun()
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
                        zip_buffer = io.BytesIO()
                        has_data = False
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                            for s_uid in student_list:
                                u_info_iter = all_users[s_uid]
                                acts_for_iter = load_json(CONFIG_FILE, {}).get("subject_activities", {}).get(view_subj, [])
                                s_ans_dict = {act: temp_ans for act in acts_for_iter if (temp_ans := get_user_activity_data(s_uid, u_info_iter.get('id',''), view_subj, u_info_iter.get('class_group',''), act, learning_data)[1])}
                                if s_ans_dict:
                                    h_content = generate_portfolio_html(s_uid, u_info_iter, view_subj, load_json(CONFIG_FILE, {}), learning_data)
                                    zip_file.writestr(f"{u_info_iter.get('class_group')}_{u_info_iter.get('name')}_포트폴리오.html", h_content.encode('utf-8-sig'))
                                    has_data = True
                        if has_data:
                            st.download_button(label=f"📦 전체 포트폴리오 일괄 다운로드 (ZIP)", data=zip_buffer.getvalue(), file_name=f"{view_subj}_{view_class}_포트폴리오.zip", mime="application/zip", type="primary")
                        st.markdown("---")
                        search_student_tab4 = st.text_input("🔍 학생 검색", key="search_student_tab4")
                        filtered_student_list = [uid for uid in student_list if search_student_tab4.lower() in f"{all_users[uid].get('class_group')} {all_users[uid].get('name')} {all_users[uid].get('id')}".lower()]
                        selected_student = st.selectbox("학생 선택", ["선택"] + filtered_student_list, format_func=lambda x: "선택" if x=="선택" else f"[{all_users[x].get('class_group')}] {all_users[x].get('name')} ({all_users[x].get('id')})")
                        if selected_student != "선택":
                            u_info_sel = all_users[selected_student]
                            acts_for_subj = load_json(CONFIG_FILE, {}).get("subject_activities", {}).get(view_subj, [])
                            filter_act = st.selectbox("활동지 필터링", ["전체 활동지 보기"] + acts_for_subj)
                            for act in acts_for_subj:
                                if filter_act != "전체 활동지 보기" and act != filter_act: continue
                                if act == ACT_3_1: render_activity1_3th(selected_student, u_info_sel, current_role)
                                elif act == ACT_3_2: render_activity2_3th(selected_student, u_info_sel, current_role)
                                elif act == ACT_3_3: render_activity3_3th(selected_student, u_info_sel, current_role)
                                elif act == ACT_2_1: render_activity1_2nd(selected_student, u_info_sel, current_role)
                                elif act == ACT_2_2: render_activity2_2nd(selected_student, u_info_sel, current_role)
                                elif act == ACT_2_3: render_activity3_2nd(selected_student, u_info_sel, current_role)
                                else: render_custom_activity(selected_student, u_info_sel, current_role, act, app_config)
                                owner_key, ans = get_user_activity_data(selected_student, u_info_sel.get('id', ''), view_subj, u_info_sel.get('class_group', ''), act, learning_data)
                                if ans:
                                    act_html = generate_activity_html(act, ans, u_info_sel.get('name'))
                                    st.download_button(label=f"📥 [{act}] 결과물 다운로드", data=act_html.encode('utf-8-sig'), file_name=f"{u_info_sel.get('name')}_{act}.html", mime="text/html", key=f"teach_dl_{selected_student}_{act}")
                                st.markdown("---")
                                
                    elif view_mode == "📅 항목별(수행평가) 전체 현황 (엑셀/HTML 다운로드)":
                        acts_for_subj = load_json(CONFIG_FILE, {}).get("subject_activities", {}).get(view_subj, [])
                        if acts_for_subj:
                            selected_view = st.selectbox("수행평가 선택", acts_for_subj)
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
                                        if not q_t and not a_t: continue
                                        if a_t: st.markdown(f"<div style='background-color:#f8f9fa; padding:8px; border-radius:5px; margin-bottom:8px;'><b>{q_t}</b><br>{a_t}</div>", unsafe_allow_html=True)
                                    single_html = generate_activity_html(selected_view, ans, u_info_csv.get('name'))
                                    zf_item.writestr(f"{u_info_csv.get('class_group')}_{u_info_csv.get('name')}_{selected_view[:8]}.html", single_html.encode('utf-8-sig'))
                                    has_html_item = True
                                    csv_data.append(["---", "---"])
                            col_down1, col_down2 = st.columns(2)
                            with col_down1:
                                if csv_data:
                                    df_csv = pd.DataFrame(csv_data)
                                    st.download_button(f"📊 엑셀(CSV) 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{view_subj}_{view_class}_{selected_view[:6]}.csv", mime='text/csv', type="primary", use_container_width=True)
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

                # 🌟 [스마트 파일 파서] 인코딩(BOM) 에러 원천 차단 및 스냅샷 통합 파일 자동 추출
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
                    str_data = json.dumps(load_json(DATA_FILE, {}), ensure_ascii=False, indent=2)
                    st.download_button("📥 학생 학습 데이터 백업 (learning_data.json)", data=str_data.encode('utf-8-sig'), file_name=f"learning_data_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json", use_container_width=True)
                    
                    str_users = json.dumps(load_json(USERS_FILE, {}), ensure_ascii=False, indent=2)
                    st.download_button("📥 회원 정보 데이터 백업 (users.json)", data=str_users.encode('utf-8-sig'), file_name=f"users_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json", use_container_width=True)
                    
                    str_config = json.dumps(load_json(CONFIG_FILE, {}), ensure_ascii=False, indent=2)
                    st.download_button("📥 시스템 설정 데이터 백업 (config.json)", data=str_config.encode('utf-8-sig'), file_name=f"config_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json", use_container_width=True)

                with col_bk2:
                    st.markdown("#### 2️⃣ 과거 시스템 DB 불러오기 (복구)")
                    
                    st.write("📂 [1] 학생 학습 데이터 복구")
                    up_data = st.file_uploader("learning_data.json 수동 복구", type="json", key="up_data", label_visibility="collapsed")
                    if st.button("학생 학습 데이터 복구 실행", use_container_width=True):
                        if up_data:
                            parsed_data = safe_parse_json(up_data, "learning_data")
                            if parsed_data is not None:
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
                
                bk_files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".json")], reverse=True)
                total_bytes = sum([os.path.getsize(os.path.join(BACKUP_DIR, f)) for f in bk_files if os.path.isfile(os.path.join(BACKUP_DIR, f))])
                total_mb = round(total_bytes / (1024 * 1024), 1)
                
                current_learning_db = load_json(DATA_FILE, {})
                active_student_count = len(current_learning_db.keys())
                
                last_bk_time = "없음"
                if bk_files:
                    try:
                        with open(os.path.join(BACKUP_DIR, bk_files[0]), "r", encoding="utf-8") as lf:
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
                st.info("💡 학생들의 데이터가 제출되거나 관리자 설정이 변경될 때마다 안전한 스냅샷이 실시간으로 생성됩니다. 과거 특정 시점으로 되돌리려면 아래 목록에서 선택 후 복원 버튼을 누르세요.")
                
                if bk_files:
                    bk_options = {}
                    for f_name in bk_files:
                        try:
                            with open(os.path.join(BACKUP_DIR, f_name), "r", encoding="utf-8") as bf:
                                b_info = json.load(bf)
                                bk_options[f_name] = f"[{b_info.get('timestamp', '알수없음')}] {b_info.get('reason', '스냅샷')} ({f_name})"
                        except: bk_options[f_name] = f_name
                            
                    selected_bk_file = st.selectbox("복원할 시점을 선택하세요", list(bk_options.keys()), format_func=lambda x: bk_options[x], label_visibility="collapsed")
                    
                    col_r1, col_r2 = st.columns([2, 1])
                    with col_r1:
                        if st.button("⏪ 선택한 시점으로 시스템 자동 복원 (1클릭 롤백)", type="primary", use_container_width=True):
                            try:
                                with open(os.path.join(BACKUP_DIR, selected_bk_file), "r", encoding="utf-8") as bf:
                                    restored_bundle = json.load(bf)
                                    if "users" in restored_bundle: save_json(USERS_FILE, restored_bundle["users"])
                                    if "learning_data" in restored_bundle: save_json(DATA_FILE, restored_bundle["learning_data"])
                                    if "config" in restored_bundle: save_json(CONFIG_FILE, restored_bundle["config"])
                                st.session_state.restore_success_auto = True
                                st.rerun()
                            except Exception as e: st.error(f"복원 중 오류 발생: {e}")
                    with col_r2:
                        if st.button("📸 지금 즉시 수동 스냅샷 생성", use_container_width=True):
                            create_auto_backup("관리자 수동 스냅샷 생성")
                            st.session_state.admin_save_success = True; st.rerun()
                else:
                    st.info("아직 생성된 백업 스냅샷이 없습니다. 학생들의 활동 제출이나 설정 저장이 일어나면 자동으로 쌓이게 됩니다.")
