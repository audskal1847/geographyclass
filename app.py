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

# 10분 단위 드롭다운 옵션 생성
TIME_OPTIONS = [f"{str(h).zfill(2)}:{str(m).zfill(2)}" for h in range(24) for m in range(0, 60, 10)]
db_lock = threading.RLock()

def get_time_index(t_str): 
    if t_str in TIME_OPTIONS: return TIME_OPTIONS.index(t_str)
    return 0

# --- [토큰 및 페이지 제어 함수 (뒤로 가기 로그아웃 방지 기능 포함)] ---
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

# 🌟 [공통] 개인정보 처리방침 렌더링 함수 (상시 고지용)
def render_privacy_policy():
    with st.expander("📜 개인정보 처리방침 (수업용 웹 앱)", expanded=False):
        st.markdown("""
        **[신선여자고등학교 수업용 웹 앱 개인정보 처리방침]**
        
        **1. 개인정보의 수집 및 이용 목적**
        - 교과 수업 운영, 학생 수행평가 과제물 제출 및 취합, 피드백 제공 및 학생부 기재 증빙
        
        **2. 수집하는 개인정보 항목**
        - 필수항목: 과목, 반, 학번, 이름, 비밀번호, 학생 작성 과제물 데이터
        - ※ 주민등록번호, 전화번호, 주소 등 불필요한 민감 정보는 일체 수집하지 않습니다.
        
        **3. 개인정보의 보유 및 이용 기간**
        - 수집된 개인정보는 원칙적으로 해당 학년도 교육과정 종료 시(익년 2월 말) 일괄 파기합니다.
        
        **4. 개인정보의 안전성 확보 조치**
        - 비밀번호 입력 시 화면 미노출 처리 및 접근 권한 통제 (교사/학생 계정 분리)
        - 데이터 유실 및 훼손 방지를 위한 자동 스냅샷 백업 시스템 운영
        
        **5. 정보주체의 권리·의무 및 행사 방법**
        - 학생은 언제든지 자신의 개인정보 및 과제 제출 내역을 열람, 정정하거나 삭제를 요구할 수 있습니다.
        - 정보 수정 및 강제 탈퇴(삭제)는 담당 교사에게 요청 시 관리자 화면을 통해 즉시 처리됩니다.
        
        **6. 만 14세 미만 아동의 개인정보 보호**
        - 본 프로그램은 고등학교 교육과정 재학생 전용이므로 만 14세 미만 아동에 해당하지 않습니다.
        
        **7. 개인정보 보호책임자**
        - 성명: 김명남 (교사) / 소속: 신선여자고등학교
        
        **8. 개인정보의 제3자 제공 및 위탁**
        - 본 프로그램은 학생의 개인정보를 제3자에게 제공하거나 외부에 위탁하지 않습니다.
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

# --- [📌 반별 수행평가 공개/비공개 여부 확인 함수] ---
def is_act_visible_for_class(act_name, class_group, config):
    vis_data = config.get("activity_visibility", {}).get(act_name, {})
    if isinstance(vis_data, dict):
        return vis_data.get(class_group, True)
    elif isinstance(vis_data, bool):
        return vis_data
    return True

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
            except json.JSONDecodeError:
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
            
            # 백업 파일 최대 30개 유지 (오래된 것 자동 정리)
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

# 🌟 [오류 수정] NameError 방지를 위한 config 인자 추가 및 자동 로드
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
        html += "</table><h4>3) 서구 중심적 시각에서 벗어나기</h4><table><tr><th>현재 가지고 있는 서구 중심적 시각</th><th>개선 방법</th></tr>"
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
                html += f"<p>첨부 파일: {c_name}</p>"
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

# --- [3] 활동지 렌더링 함수들 ---
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
    is_active, status_msg = check_active(category, user_class)
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
    is_active, status_msg = check_active(category, user_class)
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
    q3_3 = st.text_area("3-3) 만약 그런 장소(공간)이/가 있다면 무엇 때문인가?", value=ans.get("q3_3", ""), disabled=disabled_flag, key=f"q3_2_{category}")
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
    is_active, status_msg = check_active(category, user_class)
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
    is_active, status_msg = check_active(category, user_class)
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
    is_active, status_msg = check_active(category, user_class)
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
    is_active, status_msg = check_active(category, user_class)
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
    is_active, status_msg = check_active(act_name, user_class)
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
