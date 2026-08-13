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

os.makedirs(UPLOAD_DIR, exist_ok=True)

#📌 과목 및 반 목록 세팅
SUBJECTS = [
    "3학년 여행지리", 
    "2학년 도시의 미래 탐구"
]

CLASSES_MAP = {
    "3학년 여행지리": ["3B(3-6반)", "3A(3-8반)"],
    "2학년 도시의 미래 탐구": ["2G(2-1반)", "2H(2-2반)", "2I(2-8반)"]
}

#📌 단일 관리자 계정
ADMIN_ACCOUNTS = {
    "audskal": {"pw": "1847", "name": "김명남(관리자)"}
}

#📌 학년별 하드코딩 기본 활동지 6종
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

# --- [📌 반별 수행평가 타이머 및 마감 제어 로직] ---
def check_active(act_name, class_group):
    config = load_json(CONFIG_FILE, {})
    deadlines = config.get("deadlines", {}).get(act_name, {}).get(class_group, {})
    
    if not deadlines:
        return True, "💡 교사가 아직 수업 시간표를 설정하지 않았습니다. (현재 자유 입력 가능)"

    final_dl_str = deadlines.get("final_dl", "2030-12-31 23:59")
    try: final_dl = datetime.datetime.strptime(final_dl_str, "%Y-%m-%d %H:%M")
    except: final_dl = datetime.datetime.max

    now = get_kst_now()

    if now > final_dl: return False, f"🚫 최종 제출 기한({final_dl_str})이 마감되어 더 이상 작성하거나 수정할 수 없습니다."

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
                    if st_time <= current_time <= en_time: is_time_match = True
                except: continue

    sched_display = ", ".join(schedule_strs) if schedule_strs else "설정된 수업 시간 없음"
    
    if is_time_match: return True, "✅ 현재 수업 시간입니다. 정상적으로 작성하고 저장(제출)할 수 있습니다."
    else: return False, f"⏳ 현재는 정해진 수업 시간이 아닙니다. 지정된 수업 시간에만 입력할 수 있습니다.\n\n(나의 주간 수업 시간: {sched_display} / 최종 기한: {final_dl_str})"

# --- [2] 데이터 입출력 및 초기화 함수 ---
def load_json(file_path, default_value):
    with db_lock:
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f: json.dump(default_value, f, ensure_ascii=False, indent=4)
            return default_value
        for _ in range(5):
            try:
                with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
            except json.JSONDecodeError:
                import time
                time.sleep(0.1)
        return default_value

def save_json(file_path, data):
    with db_lock:
        with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=4)

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

        if users_changed: save_json(USERS_FILE, users)
        
        current_config = load_json(CONFIG_FILE, {})
        needs_update = False
        if "materials" not in current_config: current_config["materials"] = []; needs_update = True
        if "notices" not in current_config: current_config["notices"] = []; needs_update = True
        if "custom_blocks" not in current_config: current_config["custom_blocks"] = []; needs_update = True
        if "dynamic_links" not in current_config: current_config["dynamic_links"] = []; needs_update = True
            
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
            if k in current_config: del current_config[k]; needs_update = True
        if needs_update: save_json(CONFIG_FILE, current_config)

#📌 카테고리 병합(Rowspan) 처리용 HTML 생성 함수
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

#📌 모둠원 데이터 자동 연동 스캐너 함수
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
        for row in ans.get("s1_df", []): csv_data.append([row.get("대륙", ""), f"관심도: {row.get('관심도', '')} / 지식수준: {row.get('지식수준', '')}"])
        csv_data.append(["2) 특정 국가에 대한 기억과 인상 분석 (직접경험)", ""])
        for row in ans.get("direct_df", []): csv_data.append([row.get("여행해 본 국가", ""), row.get("해당 국가에 대한 구체적인 기억 혹은 인상", "")])
        csv_data.append(["간접경험 (영화/드라마)", ans.get("ind1", "")])
        csv_data.append(["간접경험 (음악/연예인)", ans.get("ind2", "")])
        csv_data.append(["간접경험 (음식)", ans.get("ind3", "")])
        csv_data.append(["3) 꼭 가 보고 싶은 Top 5 국가와 그 이유", ""])
        for row in ans.get("top5_want", []): csv_data.append([row.get("국가 혹은 지역", ""), row.get("이유", "")])
        csv_data.append(["4) 절대 가고 싫은 Top 5 국가와 그 이유", ""])
        for row in ans.get("top5_notwant", []): csv_data.append([row.get("국가 혹은 지역", ""), row.get("이유", "")])
        csv_data.append(["", ""])
        csv_data.append(["[2. 특정 대륙/국가에 대한 자신의 편견과 고정관념]", ""])
        csv_data.append(["1) 국가별 한 단어 라벨링", ""])
        for row in ans.get("label_df", []): csv_data.append([row.get("가 보고 싶은 국가", ""), f"라벨: {row.get('한 단어 라벨', '')} / 싫은 국가: {row.get('가고 싶지 않은 국가', '')} / 라벨(부정): {row.get('한 단어 라벨(부정)', '')}"])
        csv_data.append(["2) 개인적으로 가장 강한 편견을 가진 국가", ""])
        for row in ans.get("prej_df", []): csv_data.append([row.get("국가명", ""), f"편견 내용: {row.get('편견 내용', '')} / 형성 과정: {row.get('편견 형성 과정 혹은 이유', '')}"])
        csv_data.append(["3) 미디어와 교육의 영향으로 인한 인식 발견", ""])
        csv_data.append(["뉴스에서 접하는 국가들", ans.get("media1_1", "")])
        csv_data.append(["뉴스에서의 이미지", ans.get("media1_2", "")])
        csv_data.append(["영화/드라마에서 접하는 국가들", ans.get("media2_1", "")])
        csv_data.append(["영화/드라마에서의 이미지", ans.get("media2_2", "")])
        csv_data.append(["학교에서 배운 국가들", ans.get("media3_1", "")])
        csv_data.append(["학교에서 배운 지식", ans.get("media3_2", "")])
        csv_data.append(["4) 부정확한 정보나 과장된 인식 발견", ""])
        for row in ans.get("fake_df", []): csv_data.append([row.get("국가명", ""), f"잘못 알고 있던 내용: {row.get('잘못 알고 있었던 내용', '')} / 실제 사실: {row.get('실제 사실', '')}"])
        csv_data.append(["5) 우월감이나 차별 의식 점검", ""])
        for row in ans.get("discrim_df", []): csv_data.append([row.get("어떤 국가에 대해?", ""), f"어떤 측면에서: {row.get('어떤 측면에서', '')} / 이유: {row.get('그 이유', '')}"])
        csv_data.append(["", ""])
        csv_data.append(["[3. 포용적이고 균형잡힌 세계관을 위한 노력]", ""])
        for row in ans.get("change_df", []): csv_data.append([row.get("어떤 국가에 대해?", ""), f"현재 편견: {row.get('현재의 편견', '')} / 정보 수집 계획: {row.get('올바른 정보를 찾기 위한 계획', '')}"])
        csv_data.append(["가장 무관심했던 대륙/국가", ""])
        for row in ans.get("ignore_df", []): csv_data.append([row.get("선택 대륙/국가", ""), f"무관심 이유: {row.get('무관심 이유', '')} / 관심 확장 방법: {row.get('관심 확장을 위한 정보 수집 방법', '')}"])
        csv_data.append(["서구 중심적 시각 벗어나기", ""])
        for row in ans.get("western_df", []): csv_data.append([row.get("현재 가지고 있는 서구 중심적 시각", ""), f"개선 방법: {row.get('개선 방법', '')}"])
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
        for row in ans.get("step3_df", []): csv_data.append([row.get("거주 적합성 요인", ""), f"별점: {row.get('만족도 점수', '')} / 평가: {row.get('한 줄 평가', '')}"])
        csv_data.append(["1. 기존 프레임", ans.get("step4_1", "")])
        csv_data.append(["2. 지리적 본질", ans.get("step4_2", "")])
        csv_data.append(["3. 슬로건", ans.get("step4_3", "")])
        csv_data.append(["4. 개선 아이디어", ans.get("step4_4", "")])
    elif selected_view == ACT_2_2:
        csv_data.append(["모둠 구성원", f"1: {ans.get('m1_id','')} {ans.get('m1_name','')} / 2: {ans.get('m2_id','')} {ans.get('m2_name','')} / 3: {ans.get('m3_id','')} {ans.get('m3_name','')} / 4: {ans.get('m4_id','')} {ans.get('m4_name','')}"])
        csv_data.append(["1. 대상 지역", ans.get("step1_1", "")])
        for row in ans.get("step1_2_df", []): csv_data.append([row.get("구분", ""), f"항목: {row.get('필수 서비스 항목', '')} / 충분: {row.get('충분', '')} / 부족: {row.get('부족 or 없음', '')}"])
        csv_data.append(["문제점 1", ans.get("step1_3_1", "")])
        csv_data.append(["문제점 2", ans.get("step1_3_2", "")])
        csv_data.append(["문제점 3", ans.get("step1_3_3", "")])
        csv_data.append(["[도시 개조 포인트 (학생 추가 포함)]", ""])
        csv_data.append(["카테고리", "코드", "세부 개조 항목", "비용"])
        for row in ans.get("step2_point_df", []): csv_data.append([row.get("카테고리", ""), row.get("코드", ""), row.get("세부 개조 항목", ""), row.get("비용", "")])
        for row in ans.get("step2_df", []): csv_data.append([f"트레이드오프 순번 {row.get('순번', '')}", f"코드: {row.get('선택 코드', '')} / 버릴공간: {row.get('버릴 공간', '')} / 포인트: {row.get('사용 포인트', '')} / 재설계: {row.get('공간 재설계 이유 및 기대효과', '')}"])
        if ans.get("file_before_data") or ans.get("img_before"): csv_data.append(["변경 전 자료", f"제출 완료 ({ans.get('file_before_name', '스케치.png')})"])
        if ans.get("file_after_data") or ans.get("img_after"): csv_data.append(["변경 후 자료", f"제출 완료 ({ans.get('file_after_name', '스케치.png')})"])
        csv_data.append(["1. 슬로건", ans.get("step4_1", "")])
        csv_data.append(["2. 공간 문제", ans.get("step4_2", "")])
        csv_data.append(["3. 버리고 채운 것", ans.get("step4_3", "")])
        csv_data.append(["4. 일상 변화", ans.get("step4_4", "")])
    elif selected_view == ACT_2_3:
        csv_data.append(["개별 정보", f"학번: {ans.get('ind_id', '')} / 이름: {ans.get('ind_name', '')}"])
        csv_data.append(["희망 진로 혹은 계열", ans.get("ind_career", "")])
        csv_data.append(["[Step 1. 우리 지역 정체성 자원 발굴 및 팩트 체크]", ""])
        for row in ans.get("step1_df", []): csv_data.append([row.get("구분", ""), f"키워드: {row.get('내가 찾은 정체성 키워드 혹은 문장', '')} / 근거: {row.get('근거가 되는 사실·통계·사건', '')} / 출처: {row.get('출처(기관명/자료명/연도)', '')}"])
        csv_data.append(["최종 선택 키워드", ans.get("step1_keyword", "")])
        csv_data.append(["단 하나의 메시지", ans.get("step1_message", "")])
        csv_data.append(["[Step 2. 캔버스 선정]", ""])
        for row in ans.get("step2_df", []): csv_data.append([row.get("건물명", ""), f"벽면: {row.get('벽면 조건', '')} / 관람: {row.get('관람 조건', '')} / 접근성: {row.get('접근성', '')} / 제약: {row.get('예상 제약', '')} / 연관성: {row.get('정체성 연관성', '')} / 적합도: {row.get('적합도(별점)', '')}"])
        csv_data.append(["최종 선정 건물", ans.get("step2_final_building", "")])
        csv_data.append(["선정 이유", ans.get("step2_reason", "")])
        csv_data.append(["[Step 3. 주어진 조건 진단 및 대응 설계]", ""])
        for row in ans.get("step3_df", []): csv_data.append([row.get("조건 영역", ""), f"실제조건: {row.get('현장의 실제 조건 (확인한 사실)', '')} / 미치는 영향: {row.get('작품에 미치는 영향', '')} / 대응 방안: {row.get('나의 대응 방안', '')}"])
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
        for row in ans.get("step6_chk_df", []): csv_data.append([row.get("점검 항목", ""), "확인됨" if row.get("확인") else "미확인"])
        for row in ans.get("step6_ai_df", []): csv_data.append([row.get("사용한 도구명", ""), f"프롬프트: {row.get('입력한 프롬프트', '')} / 수정내용: {row.get('AI 결과물을 내가 수정·판단한 내용', '')}"])
        csv_data.append(["활동 성찰", ans.get("step6_reflection", "")])
    else:
        c_form = config.get("custom_forms", {}).get(selected_view, []) if config else []
        for q in c_form:
            csv_data.append([q["label"], ans.get(q["id"], "")])
    return csv_data

# --- [공통 HTML 포트폴리오 생성기] ---
def generate_html_content(act_name, ans):
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
        html += f"<tr><th>6-1) 소중한 사람에게 소개하고 싶은 장소</th><td>{ans.get('q6_1','')}</td></tr>"
        html += f"<tr><th>6-2) 그 이유</th><td>{ans.get('q6_2','')}</td></tr>"
        html += f"<tr><th>7-1) 나만의 비밀 장소</th><td>{ans.get('q7_1','')}</td></tr>"
        html += f"<tr><th>7-2) 그 이유</th><td>{ans.get('q7_2','')}</td></tr>"
        html += f"<tr><th>8-1) 과거로 돌아간다면 가보고 싶은 장소</th><td>{ans.get('q8_1','')}</td></tr>"
        html += f"<tr><th>8-2) 그 이유</th><td>{ans.get('q8_2','')}</td></tr></table>"
        
    elif act_name == ACT_3_3:
        html += "<h3>1. 세계 인식 수준에 대한 확인</h3>"
        html += "<h4>1) 대륙별 관심도 및 지식 수준 체크</h4><table><tr><th>대륙</th><th>관심도</th><th>지식수준</th></tr>"
        for row in ans.get("s1_df", []): html += f"<tr><td>{row.get('대륙','')}</td><td>{row.get('관심도','')}</td><td>{row.get('지식수준','')}</td></tr>"
        html += "</table><h4>2) 특정 국가에 대한 기억과 인상 분석</h4><h5>[직접 경험]</h5><table><tr><th>여행해 본 국가</th><th>구체적인 기억 혹은 인상</th></tr>"
        for row in ans.get("direct_df", []): html += f"<tr><td>{row.get('여행해 본 국가','')}</td><td>{row.get('해당 국가에 대한 구체적인 기억 혹은 인상','')}</td></tr>"
        html += f"</table><h5>[간접 경험]</h5><ul><li>즐겨 보는 외국 영화/드라마 나라 : {ans.get('ind1','')}</li><li>좋아하는 음악가/연예인 나라 : {ans.get('ind2','')}</li><li>자주 먹는 외국 음식 나라 : {ans.get('ind3','')}</li></ul>"
        html += "<h4>3) 꼭 가 보고 싶은 Top 5 국가와 그 이유</h4><table><tr><th>국가 혹은 지역</th><th>이유</th></tr>"
        for row in ans.get("top5_want", []): html += f"<tr><td>{row.get('국가 혹은 지역','')}</td><td>{row.get('이유','')}</td></tr>"
        html += "</table><h4>4) 절대 가고 싫은 Top 5 국가와 그 이유</h4><table><tr><th>국가 혹은 지역</th><th>이유</th></tr>"
        for row in ans.get("top5_notwant", []): html += f"<tr><td>{row.get('국가 혹은 지역','')}</td><td>{row.get('이유','')}</td></tr>"
        html += "</table><h3>2. 특정 대륙/국가에 대한 자신의 편견과 고정관념</h3><h4>1) 국가별 한 단어 라벨링</h4><table><tr><th>가 보고 싶은 국가</th><th>한 단어 라벨</th><th>가고 싶지 않은 국가</th><th>한 단어 라벨(부정)</th></tr>"
        for row in ans.get("label_df", []): html += f"<tr><td>{row.get('가 보고 싶은 국가','')}</td><td>{row.get('한 단어 라벨','')}</td><td>{row.get('가고 싶지 않은 국가','')}</td><td>{row.get('한 단어 라벨(부정)','')}</td></tr>"
        html += "</table><h4>2) 개인적으로 가장 강한 편견을 가진 국가</h4><table><tr><th>국가명</th><th>편견 내용</th><th>편견 형성 과정 혹은 이유</th></tr>"
        for row in ans.get("prej_df", []): html += f"<tr><td>{row.get('국가명','')}</td><td>{row.get('편견 내용','')}</td><td>{row.get('편견 형성 과정 혹은 이유','')}</td></tr>"
        html += "</table><h4>3) 미디어와 교육의 영향으로 인한 인식 발견</h4><table>"
        html += f"<tr><th>뉴스에서 자주 접하는 국가들</th><td>{ans.get('media1_1','')}</td><th>그 나라들에 대한 이미지</th><td>{ans.get('media1_2','')}</td></tr>"
        html += f"<tr><th>영화/드라마에서 자주 접하는 국가들</th><td>{ans.get('media2_1','')}</td><th>그 나라들에 대한 이미지</th><td>{ans.get('media2_2','')}</td></tr>"
        html += f"<tr><th>학교에서 많이 배운 국가들</th><td>{ans.get('media3_1','')}</td><th>그 나라들에 대한 지식</th><td>{ans.get('media3_2','')}</td></tr></table>"
        html += "<h4>4) 부정확한 정보나 과장된 인식 발견 (사실과 다른 내용들)</h4><table><tr><th>국가명</th><th>잘못 알고 있었던 내용</th><th>실제 사실</th></tr>"
        for row in ans.get("fake_df", []): html += f"<tr><td>{row.get('국가명','')}</td><td>{row.get('잘못 알고 있었던 내용','')}</td><td>{row.get('실제 사실','')}</td></tr>"
        html += "</table><h4>5) 우월감이나 차별 의식 점검</h4><table><tr><th>어떤 국가에 대해?</th><th>어떤 측면에서</th><th>그 이유</th></tr>"
        for row in ans.get("discrim_df", []): html += f"<tr><td>{row.get('어떤 국가에 대해?','')}</td><td>{row.get('어떤 측면에서','')}</td><td>{row.get('그 이유','')}</td></tr>"
        html += "</table><h3>3. 포용적이고 균형잡힌 세계관을 위한 노력</h3><h4>1) 편견을 바꾸고 싶은 국가</h4><table><tr><th>어떤 국가에 대해?</th><th>현재의 편견</th><th>올바른 정보를 찾기 위한 계획</th></tr>"
        for row in ans.get("change_df", []): html += f"<tr><td>{row.get('어떤 국가에 대해?','')}</td><td>{row.get('현재의 편견','')}</td><td>{row.get('올바른 정보를 찾기 위한 계획','')}</td></tr>"
        html += "</table><h4>2) 가장 무관심했던 대륙 혹은 국가</h4><table><tr><th>선택 대륙/국가</th><th>무관심 이유</th><th>관심 확장을 위한 정보 수집 방법</th></tr>"
        for row in ans.get("ignore_df", []): html += f"<tr><td>{row.get('선택 대륙/국가','')}</td><td>{row.get('무관심 이유','')}</td><td>{row.get('관심 확장을 위한 정보 수집 방법','')}</td></tr>"
        html += "</table><h4>3) 서구 중심적 시각에서 벗어나기</h4><table><tr><th>현재 가지고 있는 서구 중심적 시각</th><th>개선 방법</th></tr>"
        for row in ans.get("western_df", []): html += f"<tr><td>{row.get('현재 가지고 있는 서구 중심적 시각','')}</td><td>{row.get('개선 방법','')}</td></tr>"
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
        for row in ans.get("step3_df", []): html += f"<tr><td>{row.get('거주 적합성 요인','')}</td><td>{row.get('만족도 점수','')}</td><td>{row.get('한 줄 평가','')}</td></tr>"
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
        for row in ans.get("step1_2_df", []): html += f"<tr><td>{row.get('구분','')}</td><td>{row.get('필수 서비스 항목','')}</td><td>{row.get('충분','')}</td><td>{row.get('부족 or 없음','')}</td></tr>"
        html += "</table><h4>3. 선택한 지역의 핵심 문제점</h4>"
        html += f"<p><b>문제점 1:</b> {ans.get('step1_3_1','')}</p><p><b>문제점 2:</b> {ans.get('step1_3_2','')}</p><p><b>문제점 3:</b> {ans.get('step1_3_3','')}</p>"
        
        html += "<h3>Step 2. 도시 개조 포인트를 활용한 트레이드오프 설계</h3>"
        html += "<h4>[도시 개조 포인트 (학생 추가 포함)]</h4>"
        html += generate_points_html(ans.get("step2_point_df", []))
        
        html += "<h4>[트레이드오프 설계표]</h4><table><tr><th>순번</th><th>선택 코드</th><th>버릴 공간</th><th>사용 포인트</th><th>공간 재설계 이유 및 기대효과</th></tr>"
        for row in ans.get("step2_df", []): html += f"<tr><td>{row.get('순번','')}</td><td>{row.get('선택 코드','')}</td><td>{row.get('버릴 공간','')}</td><td>{row.get('사용 포인트','')}</td><td>{row.get('공간 재설계 이유 및 기대효과','')}</td></tr>"
        
        html += "<h3>Step 3. N분 도시 공간 개조 자료</h3>"
        b64_before = ans.get("file_before_data", ans.get("img_before", ""))
        name_before = ans.get("file_before_name", "변경전_스케치.png" if ans.get("img_before") else "")
        if b64_before:
            html += f"<h4>변경 전 자료: {name_before}</h4>"
            if name_before.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                html += f"<img src='data:image/png;base64,{b64_before}' style='max-width:100%; border:1px solid #ccc;'/>"
            else:
                html += "<p>(이미지 외의 파일이 등록되어 웹문서 포트폴리오 상에서는 미리보기를 제공하지 않습니다. 원본 파일을 다운로드하여 확인해 주세요.)</p>"
                
        b64_after = ans.get("file_after_data", ans.get("img_after", ""))
        name_after = ans.get("file_after_name", "변경후_스케치.png" if ans.get("img_after") else "")
        if b64_after:
            html += f"<h4>변경 후 자료: {name_after}</h4>"
            if name_after.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                html += f"<img src='data:image/png;base64,{b64_after}' style='max-width:100%; border:1px solid #ccc;'/>"
            else:
                html += "<p>(이미지 외의 파일이 등록되어 웹문서 포트폴리오 상에서는 미리보기를 제공하지 않습니다. 원본 파일을 다운로드하여 확인해 주세요.)</p>"
                
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
            html += f"<h4>컷 {i}</h4>"
            html += f"<p><b>장면 설명:</b> {ans.get(f'cut_{i}_desc','')}</p>"
            b64_file = ans.get(f"cut_{i}_file_data", "")
            file_name = ans.get(f"cut_{i}_file_name", "")
            if b64_file:
                html += f"<p>첨부된 파일: {file_name}</p>"
                if file_name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
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
        if act in ACTIVITIES: html += generate_html_content(act, ans)
        else:
            custom_form = config.get("custom_forms", {}).get(act, [])
            for q in custom_form: html += f"<h3>{q['label']}</h3><div class='content-box'>{ans.get(q['id'], '')}</div>"
    html += "</body></html>"
    return html

def generate_activity_html(act_name, ans, u_name):
    html = f"""<!DOCTYPE html><html lang="ko"><head><meta charset="UTF-8"><title>{u_name} - {act_name}</title>
    <style>body {{ font-family: 'Malgun Gothic', sans-serif; padding: 40px; line-height: 1.6; color: #333; }} h2 {{ color: #2c3e50; border-left: 5px solid #3498db; padding-left: 10px; }} h3 {{ color: #2980b9; }} table {{ width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 20px; table-layout: fixed; }} th {{ background-color: #ecf0f1; width: 30%; border: 1px solid #bdc3c7; padding: 10px; text-align: left; }} td {{ border: 1px solid #bdc3c7; padding: 10px; text-align: left; white-space: pre-wrap; }} .content-box {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; border: 1px solid #e9ecef; white-space: pre-wrap; }}</style></head><body>
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
    u_name = u_info.get("name", "")
    u_id = u_info.get("id", "")
    u_subj = u_info.get("subject", "전체")
    user_class = u_info.get("class_group", "")
    
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    
    is_active, status_msg = check_active(category, user_class)
    disabled_flag = (current_role == "학생" and not is_active)
    
    if current_role == "관리자": 
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. 이곳에서 작성한 내용은 학생 데이터와 분리되어 관리자 계정에만 안전하게 테스트 저장됩니다.")
        
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
            new_ans = {"a1_1": a1_1, "a1_2": a1_2, "a1_3": a1_3, "a2_1": a2_1, "a2_2_1": a2_2_1, "a2_2_2": a2_2_2, "a2_3_1": a2_3_1, "a2_3_2": a2_3_2, "a2_4": a2_4, "a3_1": a3_1, "a3_2": a3_2, "a3_3": a3_3, "a3_4": a3_4, "a3_5": a3_5, "a3_6": a3_6, "a3_7": a3_7, "a3_8": a3_8, "a3_9": a3_9, "a3_10": a3_10, "a3_11": a3_11}
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data); ans = new_ans
            st.balloons()
            st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 화면 저장이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p></div>", unsafe_allow_html=True)

def render_activity2_3th(user_key, u_info, current_role):
    category = ACT_3_2
    u_name = u_info.get("name", "")
    u_id = u_info.get("id", "")
    u_subj = u_info.get("subject", "전체")
    user_class = u_info.get("class_group", "")
    
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    
    is_active, status_msg = check_active(category, user_class)
    disabled_flag = (current_role == "학생" and not is_active)
    
    if current_role == "관리자": 
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. 이곳에서 작성한 내용은 학생 데이터와 분리되어 관리자 계정에만 안전하게 테스트 저장됩니다.")
        
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
            new_ans = {"q1_1": q1_1, "q1_2": q1_2, "q2_1": q2_1, "q2_2": q2_2, "q2_3": q2_3, "q3_1": q3_1, "q3_2": q3_2, "q3_3": q3_3, "q4_1": q4_1, "q4_2": q4_2, "q5_1": q5_1, "q5_2": q5_2, "q6_1": q6_1, "q6_2": q6_2, "q7_1": q7_1, "q7_2": q7_2, "q8_1": q8_1, "q8_2": q8_2}
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data); ans = new_ans
            st.balloons()
            st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 화면 저장이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p></div>", unsafe_allow_html=True)

def render_activity3_3th(user_key, u_info, current_role):
    category = ACT_3_3
    u_name = u_info.get("name", "")
    u_id = u_info.get("id", "")
    u_subj = u_info.get("subject", "전체")
    user_class = u_info.get("class_group", "")
    
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    
    is_active, status_msg = check_active(category, user_class)
    disabled_flag = (current_role == "학생" and not is_active)
    
    if current_role == "관리자": 
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. 이곳에서 작성한 내용은 학생 데이터와 분리되어 관리자 계정에만 안전하게 테스트 저장됩니다.")
        
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
            save_json(DATA_FILE, current_data); ans = new_ans
            st.balloons()
            st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 화면 저장이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p></div>", unsafe_allow_html=True)

def render_activity1_2nd(user_key, u_info, current_role):
    category = ACT_2_1
    u_name = u_info.get("name", "")
    u_id = u_info.get("id", "")
    u_subj = u_info.get("subject", "전체")
    user_class = u_info.get("class_group", "")
    
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
        st.info("💡 교사/관리자 모드입니다. 이곳에서 작성한 내용은 학생 데이터와 분리되어 관리자 계정에만 안전하게 테스트 저장됩니다.")
        
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    
    if is_member_view:
        st.info("💡 **[조회 전용]** 모둠장(대표)이 작성 및 저장한 화면을 연동하여 조회 중입니다. 수정/저장은 대표 학생만 가능합니다.")
    elif current_role == "학생":
        if disabled_flag: st.error(status_msg.replace('\n', '<br>'), icon="🚫")
        else: st.success(status_msg, icon="✅")

    m1_id, m1_name, m2_id, m2_name, m3_id, m3_name, m4_id, m4_name = render_group_members(ans, disabled_flag, category)

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 1. 우리 지역에 대한 '밈' 수집 및 지리 정보 팩트 체크 일지</h3>", unsafe_allow_html=True)
    st.markdown("▶ **교과서 13쪽 내용 中**")
    st.info("개인이 여러 장소에서 경험을 쌓으며 형성하는 주관적인 감정을 장소감이라 합니다. 이 장소감이 여러 사람에게 공유되면서 형성된 독특한 이미지가 바로 장소성이며, 이것이 확장되어 그 도시만의 독특한 특성인 도시 정체성을 만듭니다.")
    
    step1_1 = st.text_input("1. 우리가 선택한 우리 지역의 인터넷, SNS, 혹은 타 지역 친구들에게 들었던 우리 지역에 대한 유쾌한 편견이나 밈을 하나 선정 '밈'", value=ans.get("step1_1", ""), disabled=disabled_flag, key=f"step1_1_{category}")
    step1_2 = st.text_input("2. 이 밈이 대중에게 심어준 우리 지역에 대한 주관적 이미지 (편견 혹은 선입견) 예) 울산은 9시만 되면 도시 전체가 소등?", value=ans.get("step1_2", ""), disabled=disabled_flag, key=f"step1_2_{category}")
    
    st.markdown("▶ **나만의 주관적 장소감 성찰**")
    st.info("타 지역 사람들의 선입견과 달리, '우리 지역에서 나를 성장시킨 장소'나 '우리가 가장 애착을 느끼는 장소'를 적고 그에 대한 우리의 감정이나 생각을 적어 보세요.")
    
    step1_3 = st.text_input("3. 우리 모둠에게 특별한 장소감, 장소성, 도시 정체성을 주는 우리 지역의 장소", value=ans.get("step1_3", ""), disabled=disabled_flag, key=f"step1_3_{category}")
    step1_4 = st.text_area("4. 그 장소에서 느끼는 감정이나 생각", value=ans.get("step1_4", ""), disabled=disabled_flag, key=f"step1_4_{category}")
    
    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 2. 도시 발달 과정과 객관적 지표</h3>", unsafe_allow_html=True)
    st.markdown("▶ **교과서 14-15, 31-32쪽 내용 中**")
    st.info("객관적 의미의 도시는 시가지로 구성되며 2·3차 산업 비율이 높은 공간입니다. 도시는 살아있는 생명체처럼 탄생, 성장, 정체, 쇠퇴, 전환의 도시 발달 과정을 겪습니다. 울산은 시대별로 역동적인 변화를 거쳐왔습니다.")
    
    st.markdown("▶ **울산의 역사적 발달 과정 추적**\n다음 제시된 울산의 발달 역사 중 우리 조가 탐구할 시기를 선택하고, 당시 울산의 핵심 공간과 객관적 특징을 매칭해 보세요.\n- 조선시대: 울산읍성 중심의 생활권 형성 (울산동헌, 울산객사 중심)\n- 1960~70년대: 특정 공업 지구 지정 이후 정유·조선·자동차 중심의 항만 산업단지 건설\n- 1980~90년대: 택지 개발로 인한 도시 범위 확대 및 대기·수질 오염 환경 문제 발생 (태화강 물고기 폐사)\n- 2000년대~현재: 에코폴리스 울산 선언(2004) 이후 태화강 국가정원 조성 및 도시 재생 사업 추진")
    
    step2_1_period = st.radio("1. 우리 모둠이 탐구할 시기", ["조선시대", "1960~70년대", "1980~90년대", "2000년대~현재"], index=["조선시대", "1960~70년대", "1980~90년대", "2000년대~현재"].index(ans.get("step2_1_period", "조선시대")) if ans.get("step2_1_period") in ["조선시대", "1960~70년대", "1980~90년대", "2000년대~현재"] else 0, disabled=disabled_flag, horizontal=True, key=f"step2_1_period_{category}")
    step2_1_space = st.text_input("2-1. 선택한 시기의 핵심 공간", value=ans.get("step2_1_space", ""), disabled=disabled_flag, key=f"step2_1_space_{category}")
    step2_1_feat = st.text_input("2-2. 객관적 특징", value=ans.get("step2_1_feat", ""), disabled=disabled_flag, key=f"step2_1_feat_{category}")
    
    st.markdown("▶ **지리 데이터 기반 분석**\n우리 모둠이 선택한 시기 울산의 객관적 지표를 지리 정보 서비스나 통계 자료를 통해 확인해 보세요.\n- 추천 검색어: '울산광역시 통계포털', 'KOSIS 지역별 고용조사', '카카오맵/네이버맵 지적편집도'\n- 조사한 구체적 사실/통계 예시) 현재 울산의 제조업 종사자 비율이 약 40% 이상으로 전국 최고 수준이라는 점 / 태화강 수질이 생태 등급으로 회복된 지표 등")
    step2_3 = st.text_area("3. 선택한 시기의 객관적 지리 데이터 혹은 지표", value=ans.get("step2_3", ""), disabled=disabled_flag, key=f"step2_3_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 3. 살기 좋은 울산의 조건: 거주 적합성 진단</h3>", unsafe_allow_html=True)
    st.markdown("▶ **교과서 38쪽 내용 中**")
    st.info("일정한 곳에 머물러 살기 알맞은 조건이나 성질을 거주 적합성이라고 합니다. 이는 지속가능성, 이동성, 안전 및 보안, 서비스 효율성, 경제 성장, 도시 평판 등 삶의 질과 관련된 6대 요소로 이루어집니다. 개인의 연령, 직업, 가치관에 따라 선호하는 거주 적합성은 각기 다르게 나타납니다.")
    st.markdown("▶ **우리의 시선으로 본 울산의 거주 적합성 스코어보드**\n울산에서 살아가는 10대 고등학생인 여러분의 관점에서, 현재 울산의 거주 적합성 요소를 5점 만점으로 평가하고 그 까닭을 서술해 보세요.")
    
    stars = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
    default_step3 = [{"거주 적합성 요인": "경제 성장", "만족도 점수": "⭐⭐⭐⭐", "한 줄 평가": "대한민국 최대의 산업수도답게 일자리와 경제적 활력이 뛰어남"}] + [{"거주 적합성 요인": "", "만족도 점수": "⭐⭐⭐", "한 줄 평가": ""} for _ in range(4)]
    step3_df = pd.DataFrame(ans.get("step3_df", default_step3))
    edited_step3_df = st.data_editor(step3_df, column_config={"만족도 점수": st.column_config.SelectboxColumn("만족도 점수", options=stars, required=True)}, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step3_df_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 4. 우리의 방식으로 해 보는 울산 브랜딩: 정체성 리뉴얼</h3>", unsafe_allow_html=True)
    st.markdown("▶ **밈과 지리적 사실의 융합을 통한 '울산성(Ulsan-ity)' 재정의**")
    st.info("STEP 1~3의 탐구 결과를 바탕으로, 울산의 프레임을 위트 있게 깨부수는 우리 모둠만의 울산 브랜딩 슬로건과 간단한 정책(시설)을 제안해 봅시다.")
    
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
                "step3_df": edited_step3_df.to_dict('records'),
                "step4_1": step4_1, "step4_2": step4_2, "step4_3": step4_3, "step4_4": step4_4
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data); ans = new_ans
            st.balloons()
            st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 화면 저장이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p></div>", unsafe_allow_html=True)

def render_activity2_2nd(user_key, u_info, current_role):
    category = ACT_2_2
    u_name = u_info.get("name", "")
    u_id = u_info.get("id", "")
    u_subj = u_info.get("subject", "전체")
    user_class = u_info.get("class_group", "")
    
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
        st.info("💡 교사/관리자 모드입니다. 이곳에서 작성한 내용은 학생 데이터와 분리되어 관리자 계정에만 안전하게 테스트 저장됩니다.")
        
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    
    if is_member_view:
        st.info("💡 **[조회 전용]** 모둠장(대표)이 작성 및 저장한 화면을 연동하여 조회 중입니다. 수정/저장은 대표 학생만 가능합니다.")
    elif current_role == "학생":
        if disabled_flag: st.error(status_msg.replace('\n', '<br>'), icon="🚫")
        else: st.success(status_msg, icon="✅")

    m1_id, m1_name, m2_id, m2_name, m3_id, m3_name, m4_id, m4_name = render_group_members(ans, disabled_flag, category)

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 1. 우리 동네 현황 진단</h3>", unsafe_allow_html=True)
    st.markdown("▶ **도보 1분 / 반경 1km 생활권 분석**\n실제 답사와 지도 앱 내용을 통한 필수 서비스 결손 현황 체크")
    step1_1 = st.text_input("1. 대상 지역 (예: 학교 주변 인근 00아파트 00단지 일대)", value=ans.get("step1_1", ""), disabled=disabled_flag, key=f"step1_1_{category}")
    
    st.markdown("#### 2. 15분 생활권 반경 내 필수 서비스 체크리스트")
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

    st.markdown("#### 3. 선택한 지역의 핵심 문제점")
    st.info("반드시 실제 현장 답사 및 데이터에 기반한 내용을 작성할 것")
    step1_3_1 = st.text_area("문제점 1 / 데이터:", value=ans.get("step1_3_1", ""), disabled=disabled_flag, height=80, key=f"step1_3_1_{category}")
    step1_3_2 = st.text_area("문제점 2 / 데이터:", value=ans.get("step1_3_2", ""), disabled=disabled_flag, height=80, key=f"step1_3_2_{category}")
    step1_3_3 = st.text_area("문제점 3 / 데이터:", value=ans.get("step1_3_3", ""), disabled=disabled_flag, height=80, key=f"step1_3_3_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 2. 도시 개조 포인트를 활용한 트레이드오프 설계</h3>", unsafe_allow_html=True)
    st.markdown("▶ **트레이드오프 설계**")
    st.info("두 개 이상의 상충되는 요구사항(예: 성능 대 비용, 유연성 대 단순성) 사이에서 최선의 선택을 하기 위해 장단점을 저울질하고 조율하는 과정. 완벽한 설계는 존재하지 않으며, 모든 설계는 무엇인가를 얻는 대신 다른 것을 포기하는 구조를 가질 수 밖에 없음")
    st.markdown("▶ **도시 개조 포인트**\n- 기본 100포인트 부여, 포인트를 활용하여 기존의 비효율적, 차량 중심 공간을 보행자를 위한 친환경 인프라로!!\n- 새롭게 추가하는 카테고리/코드/세부 개조 항목 관련한 포인트는 최소 10pt, 최대 20pt(10~20pt)\n- 포인트는 남김 없이 모두 사용해야 함\n- 최소한의 현실 가능성은 충족할 것 예) 지하철 개통, 공항 건설... ㅠ.ㅠ")
    
    st.markdown("#### ▶ 도시 개조 포인트 (카테고리별 전용 추가 - 각 표 하단의 ➕ 버튼을 눌러 해당 영역에 직접 행을 추가하세요)")
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
        if cat_val in cat_names:
            current_cat = cat_val
        categorized_data[current_cat].append(row)
        
    edited_points_merged = []
    for i, cat in enumerate(cat_names):
        st.markdown(f"<h5 style='color:#2c3e50; margin-top:20px; margin-bottom:5px; font-size:20px !important; font-weight:800;'>🔹 {cat}</h5>", unsafe_allow_html=True)
        df_cat = pd.DataFrame(categorized_data[cat])
        edited_df_cat = st.data_editor(
            df_cat, 
            key=f"editor_cat_{i}_{category}", 
            num_rows="dynamic", 
            use_container_width=True, 
            hide_index=True,
            disabled=disabled_flag,
            column_config={
                "카테고리": None, 
                "코드": st.column_config.TextColumn("코드", required=True, width="small"),
                "세부 개조 항목": st.column_config.TextColumn("세부 개조 항목", width="large"),
                "비용": st.column_config.TextColumn("비용", width="small")
            }
        )
        cat_records = edited_df_cat.to_dict('records')
        for j, record in enumerate(cat_records):
            record["카테고리"] = cat if j == 0 else ""
            edited_points_merged.append(record)

    st.markdown("#### ▶ 도시 개조 트레이드오프 설계표")
    default_step2 = [{"순번": str(i+1), "선택 코드": "", "버릴 공간": "", "사용 포인트": "", "공간 재설계 이유 및 기대효과": ""} for i in range(8)]
    step2_df = pd.DataFrame(ans.get("step2_df", default_step2))
    edited_step2_df = st.data_editor(step2_df, hide_index=True, use_container_width=True, disabled=disabled_flag, num_rows="dynamic", key=f"step2_df_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 3. N분 도시 공간 개조 자료 스케치/기획안 업로드</h3>", unsafe_allow_html=True)
    st.info("💡 변경 전과 변경 후의 자료(스케치 사진, PDF, PPT 등 모든 형식 가능)를 각각 업로드하세요. 새로운 파일을 첨부 후 하단 '저장하기'를 누르면 기존 파일에 덮어씌워집니다.")
    
    col_img1, col_img2 = st.columns(2)
    
    with col_img1:
        st.markdown("**[변경 전 자료]**")
        b64_before = ans.get("file_before_data", ans.get("img_before", ""))
        name_before = ans.get("file_before_name", "변경전_스케치.png" if ans.get("img_before") else "")
        
        if b64_before:
            st.success(f"📎 등록된 파일: {name_before}")
            if name_before.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                st.image(base64.b64decode(b64_before), caption="변경 전 자료", use_container_width=True)
            else:
                st.info("이미지 외의 파일이 정상적으로 등록되어 있습니다.")
                
            if not disabled_flag:
                if st.button("🗑️ 변경 전 저장된 자료 삭제", key=f"del_saved_before_{category}"):
                    current_data = load_json(DATA_FILE, {}) 
                    if user_key in current_data and category in current_data[user_key]:
                        current_data[user_key][category]["img_before"] = ""
                        current_data[user_key][category]["file_before_data"] = ""
                        current_data[user_key][category]["file_before_name"] = ""
                        save_json(DATA_FILE, current_data)
                    st.rerun()

        file_before = st.file_uploader("새로운 변경 전 파일 선택", key=f"up_before_{category}", disabled=disabled_flag)
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
                st.image(base64.b64decode(b64_after), caption="변경 후 자료", use_container_width=True)
            else:
                st.info("이미지 외의 파일이 정상적으로 등록되어 있습니다.")
                
            if not disabled_flag:
                if st.button("🗑️ 변경 후 저장된 자료 삭제", key=f"del_saved_after_{category}"):
                    current_data = load_json(DATA_FILE, {}) 
                    if user_key in current_data and category in current_data[user_key]:
                        current_data[user_key][category]["img_after"] = ""
                        current_data[user_key][category]["file_after_data"] = ""
                        current_data[user_key][category]["file_after_name"] = ""
                        save_json(DATA_FILE, current_data)
                    st.rerun()

        file_after = st.file_uploader("새로운 변경 후 파일 선택", key=f"up_after_{category}", disabled=disabled_flag)
        if file_after and not disabled_flag:
            b64_after = base64.b64encode(file_after.getvalue()).decode("utf-8")
            name_after = file_after.name

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 4. 3분 공청회 발표를 위한 준비</h3>", unsafe_allow_html=True)
    st.markdown("▶ **핵심 정책 슬로건과 발표 내용 요약**")
    st.info("STEP 1~3의 탐구 결과를 바탕으로, 발표 자료를 만들어 봅시다.\n* 핵심 정책 슬로건에는 버릴공간과 문제점 + 채울 인프라와 미래 가치에 대한 내용이 반드시 들어가야 함.")
    step4_1 = st.text_input("1. 핵심 정책 슬로건", value=ans.get("step4_1", ""), disabled=disabled_flag, key=f"step4_1_{category}")
    step4_2 = st.text_area("2. 실제 답사 및 데이터로 확인한 선택한 지역의 가장 심각한 공간 문제는 무엇이라고 생각하는가?", value=ans.get("step4_2", ""), disabled=disabled_flag, key=f"step4_2_{category}")
    step4_3 = st.text_area("3. 한정된 100pt를 활용해 무엇을 버리고 무엇을 채웠는가? 그 이유는 무엇인가?", value=ans.get("step4_3", ""), disabled=disabled_flag, key=f"step4_3_{category}")
    step4_4 = st.text_area("4. 공간 재설계로 인해 일상이 어떻게 변화할 것이라고 생각하는가?", value=ans.get("step4_4", ""), disabled=disabled_flag, key=f"step4_4_{category}")

    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{category}"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "m1_id": m1_id, "m1_name": m1_name, "m2_id": m2_id, "m2_name": m2_name, "m3_id": m3_id, "m3_name": m3_name, "m4_id": m4_id, "m4_name": m4_name,
                "step1_1": step1_1, "step1_2_df": edited_step1_2_df.to_dict('records'),
                "step1_3_1": step1_3_1, "step1_3_2": step1_3_2, "step1_3_3": step1_3_3,
                "step2_point_df": edited_points_merged,
                "step2_df": edited_step2_df.to_dict('records'),
                "file_before_data": b64_before, "file_before_name": name_before,
                "file_after_data": b64_after, "file_after_name": name_after,
                "img_before": b64_before, "img_after": b64_after,
                "step4_1": step4_1, "step4_2": step4_2, "step4_3": step4_3, "step4_4": step4_4
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data); ans = new_ans
            st.balloons()
            st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 화면 저장이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p></div>", unsafe_allow_html=True)

#📌 2학년 신규 수행평가 (미디어 파사드 - 개별형 완벽 적용)
def render_activity3_2nd(user_key, u_info, current_role):
    category = ACT_2_3
    u_name = u_info.get("name", "")
    u_id = u_info.get("id", "")
    u_subj = u_info.get("subject", "전체")
    user_class = u_info.get("class_group", "")
    
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    
    is_active, status_msg = check_active(category, user_class)
    disabled_flag = (current_role == "학생" and not is_active)
    
    if current_role == "관리자": 
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. 이곳에서 작성한 내용은 학생 데이터와 분리되어 관리자 계정에만 안전하게 테스트 저장됩니다.")

    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {category}</h2>", unsafe_allow_html=True)
    
    if current_role == "학생":
        if disabled_flag: st.error(status_msg.replace('\n', '<br>'), icon="🚫")
        else: st.success(status_msg, icon="✅")

    # 📌 개별 정보 입력 (모둠 삭제, 개별 정보로 대체)
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 20px; margin-bottom: 15px;'>👤 개별 정보 입력</h3>", unsafe_allow_html=True)
    col_i1, col_i2, col_i3 = st.columns(3)
    default_id = st.session_state.user_info.get("id", "") if st.session_state.user_info.get("role") == "학생" else ""
    default_name = st.session_state.user_info.get("name", "") if st.session_state.user_info.get("role") == "학생" else ""
    
    ind_id = col_i1.text_input("학번", value=ans.get("ind_id", default_id), disabled=True if current_role == "학생" else disabled_flag, key=f"ind_id_{category}")
    ind_name = col_i2.text_input("이름", value=ans.get("ind_name", default_name), disabled=True if current_role == "학생" else disabled_flag, key=f"ind_name_{category}")
    ind_career = col_i3.text_input("희망 진로 혹은 계열", value=ans.get("ind_career", ""), disabled=disabled_flag, key=f"ind_career_{category}")
    st.markdown("---")

    st.markdown("""
    <div style="border: 2px solid #ccc; padding: 15px; border-radius: 8px; margin-bottom: 20px; background-color: #ffffff;">
        <h4 style="margin-top: 0; color: #2980b9; font-size: 20px; font-weight: 800;">들어가기 — 교과서 20쪽 「진로 탐색: 빛으로 작품을 만드는 미디어 파사드 디자이너」</h4>
        <table style="width: 100%; border-collapse: collapse;">
            <tr>
                <th style="border: 1px solid #ccc; padding: 8px; background-color: #f8f9fa; width: 20%; font-size:16px;">어떤 일을 하나요?</th>
                <td style="border: 1px solid #ccc; padding: 8px; font-size:16px; font-weight: 500;">· 건물의 형태와 주변 환경을 고려하여 특정 주제나 전달하고자 하는 메시지에 맞춘 디자인을 구상한다.<br>· 건축물의 외벽에 프로젝션, LED 스크린, 조명 등 다양한 기술을 활용하여 작품을 구현한다.<br>· 도시 환경에서 새로운 예술적 표현을 창조하고, 공공 공간을 더욱 매력적으로 변화시키는 역할을 한다.</td>
            </tr>
            <tr>
                <th style="border: 1px solid #ccc; padding: 8px; background-color: #f8f9fa; font-size:16px;">무엇을 잘해야 하나요?</th>
                <td style="border: 1px solid #ccc; padding: 8px; font-size:16px; font-weight: 500;">· 건축물의 외관을 예술적으로 재해석하고, 특정 주제나 메시지를 효과적으로 전달할 수 있는 능력<br>· 건축가, 기술자 등 다양한 분야의 전문가들과 협업할 수 있는 능력</td>
            </tr>
            <tr>
                <th style="border: 1px solid #ccc; padding: 8px; background-color: #f8f9fa; font-size:16px;">이 활동의 핵심 질문</th>
                <td style="border: 1px solid #ccc; padding: 8px; font-size:16px; font-weight: bold; color:#111;">"내가 미디어 파사드 디자이너라면, 우리 지역을 홍보하기 위해 어떤 작품을 만들 수 있을까?"<br><span style="font-weight: 500; color:#444;">→ 예쁜 영상을 만드는 활동이 아니다. 우리 지역의 정체성을 근거 있게 찾아내고, 실제 건물이 놓인 조건 안에서 실현 가능한 작품을 설계하는 활동이다.</span></td>
            </tr>
        </table>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 1. 우리 지역 정체성 자원 발굴 및 팩트 체크</h3>", unsafe_allow_html=True)
    st.markdown("""
    ▶ **교과서 13쪽 / 20쪽 내용 中**
    : 개인이 여러 장소에서 경험을 쌓으며 형성하는 주관적인 감정을 장소감이라 하고, 이것이 공유되어 형성된 독특한 이미지가 장소성이며, 확장되면 그 도시만의 특성인 도시 정체성이 됩니다.
    : 미디어 파사드 디자이너는 건물의 형태와 주변 환경을 고려하여 특정 주제나 전달하고자 하는 메시지에 맞춘 디자인을 구상하고, 건축물 외벽에 프로젝션·LED 스크린·조명 등 다양한 기술을 활용하여 작품을 구현합니다.
    
    ▶ 우리 지역의 정체성은 '느낌'이 아니라 '근거'에서 출발합니다. 아래 세 개의 축에서 키워드를 뽑고, 반드시 출처가 있는 자료로 뒷받침하세요.
    
    ▶ 내 작품의 소재가 될 지역 정체성을 세 개의 축에서 찾고, 반드시 근거 자료와 출처를 함께 적습니다.
    
    ▶ 추천 검색어: '울산광역시 통계포털', 'KOSIS 지역별 고용조사', '국가문화유산포털', '카카오맵/네이버맵 지적편집도'
    
    ※ 근거 자료와 출처가 비어 있는 칸은 점수로 인정되지 않습니다.
    """)
    st.info("💡 **(예시) 자연·생태** | 죽음의 강에서 되살아난 태화강 | 수질이 생태 등급으로 회복, 철새 서식지로 지정 | 울산시 환경 관련 통계 / 20OO")
    
    default_step1 = [
        {"구분": "1. 자연·생태", "내가 찾은 정체성 키워드 혹은 문장": "", "근거가 되는 사실·통계·사건": "", "출처(기관명/자료명/연도)": ""},
        {"구분": "2. 산업·경제", "내가 찾은 정체성 키워드 혹은 문장": "", "근거가 되는 사실·통계·사건": "", "출처(기관명/자료명/연도)": ""},
        {"구분": "3. 역사·문화", "내가 찾은 정체성 키워드 혹은 문장": "", "근거가 되는 사실·통계·사건": "", "출처(기관명/자료명/연도)": ""}
    ]
    step1_df = pd.DataFrame(ans.get("step1_df", default_step1))
    st.markdown("**(표의 아래쪽을 클릭하면 자유롭게 행을 추가할 수 있습니다.)**")
    edited_step1_df = st.data_editor(step1_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step1_df_{category}")
    
    step1_keyword = st.text_input("▶ 내가 최종 선택한 핵심 키워드 혹은 문장", value=ans.get("step1_keyword", ""), disabled=disabled_flag, key=f"step1_kw_{category}")
    step1_message = st.text_area("▶ 내 작품이 전할 단 하나의 메시지 (한 문장으로 쓸 것)", value=ans.get("step1_message", ""), disabled=disabled_flag, key=f"step1_msg_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 2. 캔버스 선정 — 어떤 건축물에 어떤 형태의 빛을 입힐 것인가?</h3>", unsafe_allow_html=True)
    st.markdown("""
    ▶ 우리 지역의 실제 건축물·구조물 3곳을 후보로 조사하고 비교한 뒤 최종 1곳을 선정합니다.
    ▶ 직접 답사하거나 지도 로드뷰로 확인한 내용을 적습니다. 상상으로 쓴 내용은 인정되지 않습니다.
    (현장 답사가 어려운 경우 지도 앱의 로드뷰·위성사진으로 대체하되, 캡처 화면을 반드시 첨부할 것)
    """)
    
    stars = ["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"]
    default_step2 = [
        {"건물명": "후보 1: ", "벽면 조건": "", "관람 조건": "", "접근성": "", "예상 제약": "", "정체성 연관성": "", "적합도(별점)": "⭐⭐⭐"},
        {"건물명": "후보 2: ", "벽면 조건": "", "관람 조건": "", "접근성": "", "예상 제약": "", "정체성 연관성": "", "적합도(별점)": "⭐⭐⭐"},
        {"건물명": "후보 3: ", "벽면 조건": "", "관람 조건": "", "접근성": "", "예상 제약": "", "정체성 연관성": "", "적합도(별점)": "⭐⭐⭐"}
    ]
    step2_df = pd.DataFrame(ans.get("step2_df", default_step2))
    edited_step2_df = st.data_editor(step2_df, column_config={"적합도(별점)": st.column_config.SelectboxColumn("적합도(별점)", options=stars, required=True)}, use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step2_df_{category}")

    step2_final_building = st.text_input("▶ 최종 선정 건물", value=ans.get("step2_final_building", ""), disabled=disabled_flag, key=f"step2_final_{category}")
    step2_reason = st.text_area("▶ 이유 (Step 1의 정체성 키워드 혹은 문장과 연결하여 서술할 것)", value=ans.get("step2_reason", ""), disabled=disabled_flag, key=f"step2_rsn_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 3. 주어진 조건 진단 및 대응 설계</h3>", unsafe_allow_html=True)
    st.markdown("""
    ▶ 내가 선정한 건물과 그 주변에는 내가 바꿀 수 없는 조건들이 이미 존재합니다.
    ▶ 조건을 없애거나 무시하는 것이 아니라, 그 조건을 그대로 받아들인 상태에서 어떻게 작품을 성립시킬지 설계합니다.
    ※ 중요: 조건을 "문제점"으로만 적고 끝내면 점수를 받지 못합니다. 반드시 「나의 대응 방안」까지 채워야 합니다.
    ※ 제약을 오히려 작품의 조형 요소로 뒤집어 활용한 경우 가장 높은 평가를 받습니다.
    """)
    st.info("💡 **(예시) 물리적 조건** | 외벽의 40%가 창문이라 영상이 끊겨 보인다 | 인물이나 글자를 크게 넣으면 형태가 깨진다 | 창틀 격자를 공장 창문으로 역이용해, 격자 사이로 빛이 번지는 산업 도시 이미지를 연출한다")
    
    default_step3 = [
        {"조건 영역": "1. 물리적 조건 (형태·재질·구조물)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "2. 빛·환경 조건 (주변 조명·간판·빛공해)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "3. 시간·계절 조건 (일몰 시각·강수·바람)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "4. 주민·이웃 조건 (인접 주거·상가·소음)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "5. 행정·비용 조건 (허가·예산·관리 주체)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "6. 접근·안전 조건 (동선·차도·배리어프리)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""}
    ]
    step3_df = pd.DataFrame(ans.get("step3_df", default_step3))
    st.markdown("**(표의 아래쪽을 클릭하면 자유롭게 행을 추가할 수 있습니다.)**")
    edited_step3_df = st.data_editor(step3_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step3_df_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 4. 작품 스토리보드 4컷</h3>", unsafe_allow_html=True)
    st.markdown("""
    ▶ Step 1의 메시지를 Step 2의 벽면 위에, Step 3의 조건을 지키면서 어떻게 펼칠지 4컷으로 구성합니다.
    ▶ 그림 실력은 평가하지 않습니다. 도형과 화살표로 표현해도 됩니다. 대신 설명은 구체적으로 씁니다.
    """)
    
    cut_titles = ["1. 도입", "2. 전개", "3. 절정", "4. 마무리"]
    new_cuts = {}
    
    for i in range(1, 5):
        st.markdown(f"**[ {cut_titles[i-1]} ]**")
        col_c1, col_c2 = st.columns([1, 1])
        with col_c1:
            st.markdown("화면 구성 (모든 형태의 파일 업로드 가능)")
            b64_file = ans.get(f"cut_{i}_file_data", "")
            file_name = ans.get(f"cut_{i}_file_name", "")
            
            if b64_file:
                st.success(f"📎 등록된 파일: {file_name}")
                if file_name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                    st.image(base64.b64decode(b64_file), use_container_width=True)
                else:
                    st.info("이미지 외의 파일이 정상적으로 등록되어 있습니다.")
                    
                if not disabled_flag:
                    if st.button(f"🗑️ 컷 {i} 자료 삭제", key=f"del_cut_{i}_{category}"):
                        current_data = load_json(DATA_FILE, {}) 
                        if user_key in current_data and category in current_data[user_key]:
                            current_data[user_key][category][f"cut_{i}_file_data"] = ""
                            current_data[user_key][category][f"cut_{i}_file_name"] = ""
                            save_json(DATA_FILE, current_data)
                        st.rerun()

            file_cut = st.file_uploader(f"컷 {i} 파일 첨부", key=f"up_cut_{i}_{category}", disabled=disabled_flag, label_visibility="collapsed")
            if file_cut and not disabled_flag:
                b64_file = base64.b64encode(file_cut.getvalue()).decode("utf-8")
                file_name = file_cut.name
            
            new_cuts[f"cut_{i}_file_data"] = b64_file
            new_cuts[f"cut_{i}_file_name"] = file_name
            
        with col_c2:
            st.markdown("장면 설명 · 사용 기술 · 소요 시간")
            new_cuts[f"cut_{i}_desc"] = st.text_area(f"컷 {i} 설명", value=ans.get(f"cut_{i}_desc", ""), height=150, disabled=disabled_flag, label_visibility="collapsed", key=f"desc_cut_{i}_{category}")
        st.write("")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 5. 작품 설명 카드 작성 및 갤러리 워크</h3>", unsafe_allow_html=True)
    st.markdown("▶ 완성한 작품을 전시장에 걸 때 옆에 붙는 캡션 패널을 직접 씁니다. 교실 벽에 게시하여 서로 감상합니다.")
    step5_title = st.text_input("▶ 작품 제목 (관람객의 눈길을 끌 수 있도록. 부제를 붙여도 좋다.)", value=ans.get("step5_title", ""), disabled=disabled_flag, key=f"step5_title_{category}")
    step5_place = st.text_input("▶ 전시 장소 (건물명 / 투사 벽면 / 권장 관람 위치)", value=ans.get("step5_place", ""), disabled=disabled_flag, key=f"step5_place_{category}")
    step5_summary = st.text_area("▶ 작품 개요 (3문장 이내)", value=ans.get("step5_summary", ""), disabled=disabled_flag, key=f"step5_sum_{category}")
    step5_identity = st.text_area("▶ 이 작품이 지역의 어떤 정체성을 담았는가 (Step 1의 근거 자료를 인용하여 쓸 것)", value=ans.get("step5_identity", ""), disabled=disabled_flag, key=f"step5_id_{category}")
    step5_condition = st.text_area("▶ 현장 조건을 어떻게 작품에 반영했는가 (Step 3 에서 가장 잘 해결한 조건 1가지를 골라 쓸 것)", value=ans.get("step5_condition", ""), disabled=disabled_flag, key=f"step5_cond_{category}")
    step5_change = st.text_area("▶ 이 작품이 우리 지역에 남길 변화 (관람객/주민/상권 세 측면에서 각각 한 줄씩)", value=ans.get("step5_change", ""), disabled=disabled_flag, key=f"step5_chg_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 6. 제출 전 자기 점검 및 활용 기록</h3>", unsafe_allow_html=True)
    
    checklist_items = [
        "Step 1의 정체성 키워드 3개 모두에 출처를 적었다.",
        "후보 건축물 3곳을 실제로 답사하거나 로드뷰로 확인했다.",
        "Step 3의 6개 조건 영역을 빈칸 없이 채웠다.",
        "조건을 문제점으로만 쓰지 않고, 대응 방안까지 모두 적었다.",
        "스토리보드 4컷이 Step 1의 메시지와 연결되어 있다.",
        "진로 심화 트랙 산출물을 함께 제출했다.",
        "작품 설명 카드에 근거 자료를 인용했다."
    ]
    default_checklist = [{"No": i+1, "점검 항목": item, "확인": False} for i, item in enumerate(checklist_items)]
    step6_chk_df = pd.DataFrame(ans.get("step6_chk_df", default_checklist))
    edited_step6_chk_df = st.data_editor(
        step6_chk_df, 
        hide_index=True, 
        use_container_width=True, 
        disabled=disabled_flag, 
        key=f"step6_chk_{category}",
        column_config={
            "No": st.column_config.NumberColumn("No", width="small"),
            "확인": st.column_config.CheckboxColumn("확인", default=False)
        }
    )

    st.markdown("#### ▶ 생성형 AI 활용 기록 (자유 행 추가)")
    st.info("이미지·아이디어 생성에 AI를 사용한 경우 반드시 기록. 미기재 시 평가에서 제외됩니다.")
    default_ai = [{"사용한 도구명": "", "입력한 프롬프트": "", "AI 결과물을 내가 수정·판단한 내용": ""}]
    step6_ai_df = pd.DataFrame(ans.get("step6_ai_df", default_ai))
    st.markdown("**(표의 아래쪽을 클릭하면 자유롭게 행을 추가할 수 있습니다.)**")
    edited_step6_ai_df = st.data_editor(step6_ai_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step6_ai_{category}")
    
    step6_reflection = st.text_area("▶ 활동 성찰 (이 활동으로 도시와 나의 진로에 대해 새로 알게 된 점)", value=ans.get("step6_reflection", ""), height=150, disabled=disabled_flag, key=f"step6_ref_{category}")

    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{category}"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "ind_id": ind_id, "ind_name": ind_name, "ind_career": ind_career,
                "step1_df": edited_step1_df.to_dict('records'),
                "step1_keyword": step1_keyword,
                "step1_message": step1_message,
                "step2_df": edited_step2_df.to_dict('records'),
                "step2_final_building": step2_final_building,
                "step2_reason": step2_reason,
                "step3_df": edited_step3_df.to_dict('records'),
                "step5_title": step5_title, "step5_place": step5_place,
                "step5_summary": step5_summary, "step5_identity": step5_identity,
                "step5_condition": step5_condition, "step5_change": step5_change,
                "step6_chk_df": edited_step6_chk_df.to_dict('records'),
                "step6_ai_df": edited_step6_ai_df.to_dict('records'),
                "step6_reflection": step6_reflection
            }
            new_ans.update(new_cuts)
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data); ans = new_ans
            st.balloons()
            st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 화면 저장이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p></div>", unsafe_allow_html=True)


def render_custom_activity(user_key, u_info, current_role, act_name, config):
    u_name = u_info.get("name", "")
    u_id = u_info.get("id", "")
    u_subj = u_info.get("subject", "전체")
    user_class = u_info.get("class_group", "")
    
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, act_name, learning_data)
    
    is_active, status_msg = check_active(act_name, user_class)
    disabled_flag = (current_role == "학생" and not is_active)
    
    if current_role == "관리자": 
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. 이곳에서 작성한 내용은 학생 데이터와 분리되어 관리자 계정에만 안전하게 테스트 저장됩니다.")

    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ {act_name}</h2>", unsafe_allow_html=True)
    st.markdown("---")
    if current_role == "학생":
        if disabled_flag: st.error(status_msg.replace('\n', '<br>'), icon="🚫")
        else: st.success(status_msg, icon="✅")

    custom_form = config.get("custom_forms", {}).get(act_name, [])
    if not custom_form: st.info("등록된 질문(문항)이 없습니다. 관리자 화면에서 문항을 추가해주세요.")

    new_ans = {}
    for q in custom_form:
        q_id, q_label, q_type = q["id"], q["label"], q["type"]
        st.markdown(f"**{q_label}**")
        if q_type == "text": new_ans[q_id] = st.text_input(f"{q_label} 입력", value=ans.get(q_id, ""), disabled=disabled_flag, label_visibility="collapsed", key=f"{q_id}_{act_name}")
        elif q_type == "textarea": new_ans[q_id] = st.text_area(f"{q_label} 입력", value=ans.get(q_id, ""), height=150, disabled=disabled_flag, label_visibility="collapsed", key=f"{q_id}_{act_name}")
        st.markdown("<br>", unsafe_allow_html=True)

    if not disabled_flag:
        if st.button("저장하기", type="primary", key=f"save_{act_name}"):
            current_data = load_json(DATA_FILE, {})
            if user_key not in current_data: current_data[user_key] = {}
            current_data[user_key][act_name] = new_ans
            save_json(DATA_FILE, current_data); ans = new_ans
            st.balloons()
            st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 화면 저장이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p></div>", unsafe_allow_html=True)

    if ans:
        st.markdown("---")
        html_data = f"<!DOCTYPE html><html lang='ko'><head><meta charset='UTF-8'><title>{u_name}</title><style>body {{ font-family: 'Malgun Gothic', sans-serif; padding: 40px; line-height: 1.6; color: #333; }} h2 {{ color: #2c3e50; border-left: 5px solid #3498db; padding-left: 10px; }} h3 {{ color: #2980b9; }} .content-box {{ background-color: #f8f9fa; padding: 15px; border-radius: 5px; border: 1px solid #e9ecef; white-space: pre-wrap; margin-bottom: 20px; }}</style></head><body><div style='text-align: right; margin-bottom: 20px;'><b>이름:</b> {u_name}</div><h2>▶ {act_name}</h2>"
        for q in custom_form: html_data += f"<h3>{q['label']}</h3><div class='content-box'>{ans.get(q['id'],'')}</div>"
        html_data += "</body></html>"
        st.download_button("📥 내 작성 내용 다운로드 (웹문서)", data=html_data.encode('utf-8-sig'), file_name=f"{u_name}_{act_name}.html", mime="text/html")

def render_class_overview(current_role, u_info, view_subj):
    st.markdown(f"<h2 style='font-size: 28px; font-weight: 900; color: #000; margin-bottom: 20px;'>🎯 [{view_subj}] 수행평가 및 활동 모듈</h2>", unsafe_allow_html=True)
    st.markdown("---")
    app_config = load_json(CONFIG_FILE, {})
    
    custom_blocks = [b for b in app_config.get("custom_blocks", []) if b.get("subject", "전체 공지") in ["전체 공지", view_subj]]
    for block in custom_blocks:
        block_content_html = block["content"].replace('\n', '<br>')
        st.markdown(f"""
        <div style="border: 2px solid #4CAF50; border-radius: 8px; margin-bottom: 25px; background-color: #ffffff;">
            <div style="background-color: #4CAF50; color: white; padding: 12px 20px; font-size: 24px; font-weight: 900; border-top-left-radius: 4px; border-top-right-radius: 4px;">
                {block["title"]}
            </div>
            <div style="padding: 20px; font-size: 19px; font-weight: 700; color: #111; line-height: 1.6;">
                {block_content_html}
            </div>
        </div>
        """, unsafe_allow_html=True)

    dynamic_links = [l for l in app_config.get("dynamic_links", []) if l.get("subject", "전체 공지") in ["전체 공지", view_subj]]
    if dynamic_links:
        grouped_links = {}
        for link in dynamic_links:
            grouped_links.setdefault(link['group'], []).append(link)
        
        st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111;'>🔗 바로가기 링크</h3>", unsafe_allow_html=True)
        link_cols = st.columns(2)
        col_idx = 0
        for group_name, links in grouped_links.items():
            with link_cols[col_idx % 2]:
                with st.expander(group_name, expanded=True):
                    for link in links:
                        st.markdown(f"**[{link['title']}]({link['url']})**")
            col_idx += 1
        st.markdown("---")

    notices = [n for n in app_config.get("notices", []) if n.get("subject", "전체 공지") in ["전체 공지", view_subj]]
    if notices:
        st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111;'>📢 알림 및 공지사항</h3>", unsafe_allow_html=True)
        for notice in notices:
            t, c = notice.get("제목", "").strip(), notice.get("내용", "").strip()
            if t or c: st.info(f"**{t}**\n\n{c}")
        st.markdown("---")

    materials = app_config.get("materials", [])
    if materials:
        st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111;'>👨‍🏫 수업 공지 및 자료실</h3>", unsafe_allow_html=True)
        for mat in materials:
            if mat.get("subject", "전체 공지") in ["전체 공지", view_subj]:
                if mat["type"] == "link": st.markdown(f"🔗 **[{mat['title']}]({mat['content']})**")
                elif mat["type"] == "file" and os.path.exists(mat["content"]):
                    with open(mat["content"], "rb") as f: st.download_button(f"📥 {mat['title']} ({mat['filename']}) 다운로드", f, file_name=mat['filename'], key=f"mat_dl_{mat['id']}")
        st.markdown("---")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111;'>📝 학년별 수행평가 목록</h3>", unsafe_allow_html=True)
    st.caption("아래 버튼을 눌러 해당 수행평가 작성 화면으로 이동하세요.")
    acts_for_subj = app_config.get("subject_activities", {}).get(view_subj, [])
    if acts_for_subj:
        cols = st.columns(3)
        for idx, act in enumerate(acts_for_subj):
            with cols[idx % 3]:
                if st.button(f"📄 {act}", use_container_width=True, key=f"btn_go_{act}"): change_page(act)
    else: st.info("아직 이 과목에 할당된 수행평가 목록이 없습니다.")

# --- [자동 저장 및 Keep-Alive 기능 주입] ---
def inject_custom_scripts():
    components.html("""
    <script>
    document.addEventListener("DOMContentLoaded", function() {
        const parentDoc = window.parent.document;
        
        function initAutoSave() {
            const elements = parentDoc.querySelectorAll('input[type="text"], textarea');
            elements.forEach(el => {
                const ariaLabel = el.getAttribute('aria-label') || '';
                const key = 'autosave_' + window.parent.location.pathname + '_' + ariaLabel;
                
                if (!el.dataset.autosaveAttached && ariaLabel !== '') {
                    el.dataset.autosaveAttached = "true";
                    
                    el.addEventListener('input', () => {
                        window.localStorage.setItem(key, el.value);
                    });
                    
                    el.addEventListener('focus', () => {
                        const savedVal = window.localStorage.getItem(key);
                        if (savedVal && el.value === "") {
                            let setter = Object.getOwnPropertyDescriptor(window.parent.HTMLInputElement.prototype, "value")?.set;
                            if(el.tagName === 'TEXTAREA') {
                                setter = Object.getOwnPropertyDescriptor(window.parent.HTMLTextAreaElement.prototype, "value")?.set;
                            }
                            if(setter) {
                                setter.call(el, savedVal);
                                el.dispatchEvent(new Event('input', { bubbles: true }));
                            } else {
                                el.value = savedVal; 
                            }
                        }
                    });
                }
            });
        }
        setInterval(initAutoSave, 1500);

        setInterval(() => {
            fetch(window.parent.location.href, { cache: "no-store" });
        }, 180000); 
    });
    </script>
    """, height=0, width=0)

st.set_page_config(page_title="수업 및 활동 어시스트 프로그램", layout="wide")

st.markdown("""
<style>
/* 제목(Header) 계층 명확화 */
.stMarkdown h1 { font-size: 34px !important; font-weight: 900 !important; color: #000000 !important; margin-bottom: 20px !important; }
.stMarkdown h2 { font-size: 28px !important; font-weight: 900 !important; color: #000000 !important; margin-top: 10px !important; margin-bottom: 15px !important; padding-bottom: 8px !important; border-bottom: 2px solid #dddddd !important; }
.stMarkdown h3 { font-size: 24px !important; font-weight: 800 !important; color: #111111 !important; margin-top: 25px !important; margin-bottom: 10px !important; }
.stMarkdown h4 { font-size: 20px !important; font-weight: 800 !important; color: #222222 !important; margin-top: 20px !important; margin-bottom: 10px !important; }
.stMarkdown h5 { font-size: 18px !important; font-weight: 700 !important; color: #333333 !important; }

/* 본문 텍스트 */
div[data-testid="stMarkdownContainer"] > p, div[data-testid="stMarkdownContainer"] > ul > li { 
    font-size: 16px !important; 
    font-weight: 500 !important; 
    color: #333333 !important; 
    line-height: 1.6 !important; 
}
.stMarkdown strong, .stMarkdown b { font-weight: 700 !important; color: #000000 !important; }

/* 폼 입력창 라벨 (질문 내용) */
label p { font-size: 16px !important; font-weight: 700 !important; color: #111111 !important; }
input, textarea, div[data-baseweb="select"] { font-size: 16px !important; font-weight: 500 !important; color: #222222 !important; }

/* 사이드바 */
[data-testid="stSidebar"] .stMarkdown p { font-size: 16px !important; font-weight: 600 !important; color: #222222 !important; }

/* 사이드바 내부 폼(Form) 테두리 제거 */
[data-testid="stSidebar"] [data-testid="stForm"] {
    border: none !important;
    padding: 0 !important;
    background-color: transparent !important;
}

/* 메인 버튼 스타일링 */
[data-testid="stFormSubmitButton"] button, button[kind="primary"] { background-color: #FF4B4B !important; border: none !important; border-radius: 8px !important; min-height: 50px !important; width: 100% !important; padding: 12px !important; }
[data-testid="stFormSubmitButton"] button p, button[kind="primary"] p, [data-testid="stFormSubmitButton"] button div, button[kind="primary"] div { color: #ffffff !important; font-size: 18px !important; font-weight: 800 !important; }

button[kind="secondary"] { background-color: #0056b3 !important; border: none !important; border-radius: 8px !important; min-height: 50px !important; width: 100% !important; padding: 12px !important; }
button[kind="secondary"] p, button[kind="secondary"] div { color: #ffffff !important; font-size: 18px !important; font-weight: 800 !important; }

/* 데이터 테이블 */
[data-testid="stDataFrame"] { border: 2px solid #ccc !important; border-radius: 5px; }
table th { background-color: #f1f3f5 !important; font-size: 15px !important; font-weight: 800 !important; text-align:center !important; color: #111 !important; }
table td { font-size: 15px !important; font-weight: 500 !important; color: #333 !important; }

/* 안내 메시지 */
[data-testid="stAlert"] div[data-testid="stMarkdownContainer"] p { font-size: 16px !important; font-weight: 600 !important; color: #111 !important; }
</style>
""", unsafe_allow_html=True)

init_system()

# 뒤로가기 방지용 세션 토큰 체커 및 저장
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

st.sidebar.title("🔒 인증 센터")

if st.session_state.logged_in:
    u_info = st.session_state.user_info
    if u_info['role'] == "관리자": 
        sidebar_html = f"<div style='background-color:#e8f4f8; padding:10px; border-radius:5px; margin-bottom:15px; line-height:1.4;'><div style='font-size:16px; font-weight:bold; color:#0056b3; margin-bottom:3px;'>🟢 {u_info['name']} 님 로그인 중</div><div style='font-size:15px; font-weight:600; color:#333; margin-bottom:2px;'>📘 과목: {u_info.get('subject', '전체')}</div><div style='font-size:15px; font-weight:600; color:#333;'>🛡️ 권한: {u_info['role']}</div></div>"
        st.sidebar.markdown(sidebar_html, unsafe_allow_html=True)
        st.session_state.admin_view_subject = st.sidebar.selectbox("👀 관리 및 미리보기 과목", ["전체 공지"] + SUBJECTS)
    else: 
        sidebar_html = f"<div style='background-color:#e8f4f8; padding:10px; border-radius:5px; margin-bottom:15px; line-height:1.4;'><div style='font-size:16px; font-weight:bold; color:#0056b3; margin-bottom:3px;'>🟢 {u_info['name']} 님 로그인 중</div><div style='font-size:15px; font-weight:600; color:#333; margin-bottom:2px;'>📘 과목: {u_info.get('subject', '전체')}</div><div style='font-size:15px; font-weight:600; color:#333; margin-bottom:2px;'>🏫 소속: {u_info.get('class_group', '')}</div><div style='font-size:15px; font-weight:600; color:#333;'>🛡️ 권한: {u_info['role']}</div></div>"
        st.sidebar.markdown(sidebar_html, unsafe_allow_html=True)
        
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
                        admin_info = ADMIN_ACCOUNTS[input_id]
                        st.session_state.logged_in = True
                        st.session_state.user_info = {"user_key": input_id, "id": input_id, "name": admin_info["name"], "role": "관리자", "subject": "전체", "class_group": "관리자"}
                        st.query_params["session_token"] = encode_token(input_id)
                        st.rerun()
                    else: st.error("❌ 관리자 정보가 틀렸습니다.")

st.sidebar.markdown("---")
st.sidebar.markdown("<div style='text-align: center; color: #222; font-size: 18px; font-weight: 900;'>Made by<br><span style='font-size: 24px; color: #000; font-weight: 900;'>신선여자고등학교 김명남</span></div>", unsafe_allow_html=True)

# 실시간 오토 세이브 및 세션 유지 기능 삽입
inject_custom_scripts()

# --- 🔍 학생 검색 필터 헬퍼 함수 ---
def filter_students(user_dict, search_term, approved_only=True):
    filtered = {}
    for k, v in user_dict.items():
        if v.get("role") != "학생": continue
        if approved_only and not v.get("approved", True): continue
        
        search_target = f"{v.get('subject','')} {v.get('class_group','')} {v.get('name','')} {v.get('id','')}"
        if search_term.lower() in search_target.lower():
            filtered[k] = v
    return filtered

if not st.session_state.logged_in:
    st.markdown("<h1 style='font-size:36px; font-weight:900; color:#000;'>🏫 수업 및 활동 어시스트 프로그램</h1>", unsafe_allow_html=True)
    st.info("왼쪽 사이드바를 이용해 로그인해주세요.")
else:
    current_role = st.session_state.user_info["role"]
    current_user_key = st.session_state.user_info["user_key"]
    u_info = st.session_state.user_info
    user_class_group = u_info.get('class_group', '')
    
    app_config = load_json(CONFIG_FILE, {})
    learning_data = load_json(DATA_FILE, {})

    if st.session_state.current_page != "main":
        act_name = st.session_state.current_page
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
            st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111;'>📄 개별 활동 결과물 다운로드</h3>", unsafe_allow_html=True)
            st.caption("제출 완료한 활동지만 나타납니다.")
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
            st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111;'>📚 내 포트폴리오 일괄 다운로드</h3>", unsafe_allow_html=True)
            html_content_all = generate_portfolio_html(current_user_key, u_info, u_info['subject'], app_config, learning_data)
            st.download_button(label=f"📦 {u_info['name']} 학생 전체 포트폴리오 일괄 다운로드 (웹문서)", data=html_content_all.encode('utf-8-sig'), file_name=f"{u_info['name']}_전체_포트폴리오.html", mime="text/html", type="primary")
            st.caption("💡 다운로드한 파일을 인터넷 창으로 연 뒤 **[우클릭 ➔ 인쇄 ➔ PDF로 저장]** 하시면 제출용 파일이 완성됩니다.")

        elif current_role == "관리자":
            st.markdown("<h1 style='font-size:36px; font-weight:900; color:#000;'>🛠️ 관리자(교사) 대시보드</h1>", unsafe_allow_html=True)
            menu_tabs = st.tabs(["📌 메인 화면/기한 설정", "🗂️ 수행평가 문항 제작", "👥 회원 관리", "📥 학생 제출 자료 조회 및 관리", "💾 DB 백업 및 복구"])
            
            with menu_tabs[0]:
                if st.session_state.get("admin_save_success", False):
                    st.balloons()
                    st.markdown("<div style='text-align:center; padding:30px; background-color:#e8f5e9; border-radius:8px; border:2px solid #4CAF50; margin:20px 0;'><h2 style='margin:0 0 15px 0; font-size:26px; font-weight:900; color:#111;'>🎉 화면 저장이 완료되었습니다!</h2><p style='margin:0; font-size:18px; font-weight:700; color:#111;'>변경하신 내용이 안전하게 저장되어 즉시 반영됩니다.</p></div>", unsafe_allow_html=True)
                    st.session_state.admin_save_success = False

                admin_view_subj = st.session_state.get("admin_view_subject", "전체 공지")
                
                st.markdown(f"<h3 style='font-size: 24px; font-weight: 800; margin-top:20px;'>🖥️ [{admin_view_subj}] 학생 화면 미리보기 및 활동지 테스트</h3>", unsafe_allow_html=True)
                render_class_overview(current_role, u_info, admin_view_subj)
                st.markdown("---")
                
                st.markdown(f"<h3 style='font-size: 24px; font-weight: 800; margin-top:20px;'>⚙️ [{admin_view_subj}] 메인 화면 편집 및 기한 설정</h3>", unsafe_allow_html=True)
                fresh_config = load_json(CONFIG_FILE, {})
                
                st.markdown("#### 📝 자유 텍스트/공지 블록 추가 (메인 화면)")
                st.info("💡 링크 외에도 안내문, 팁, 텍스트 등 원하는 내용을 박스 형태로 메인 화면에 자유롭게 추가할 수 있습니다.")
                col_cb1, col_cb2 = st.columns(2)
                with col_cb1:
                    st.write("➕ **새로운 블록 만들기**")
                    with st.form("add_custom_block"):
                        cb_title = st.text_input("블록 제목 (예: 📢 내일 수업 준비물 안내)")
                        cb_content = st.text_area("내용 입력 (엔터 및 줄바꿈 지원)")
                        if st.form_submit_button("블록 생성하기", type="primary"):
                            if cb_title and cb_content:
                                new_block = {"id": f"cb_{datetime.datetime.now().strftime('%d%H%M%S')}", "title": cb_title, "content": cb_content, "subject": admin_view_subj}
                                if "custom_blocks" not in fresh_config: fresh_config["custom_blocks"] = []
                                fresh_config["custom_blocks"].append(new_block)
                                save_json(CONFIG_FILE, fresh_config)
                                st.session_state.admin_save_success = True; st.rerun()
                            else: st.warning("제목과 내용을 모두 입력해주세요.")
                with col_cb2:
                    st.write("❌ **기존 블록 삭제**")
                    current_blocks = [b for b in fresh_config.get("custom_blocks", []) if b.get("subject", "전체 공지") == admin_view_subj]
                    if current_blocks:
                        del_cb_target = st.selectbox("삭제할 블록 선택", current_blocks, format_func=lambda x: x["title"])
                        if st.button("선택한 블록 삭제하기", type="primary"):
                            other_blocks = [b for b in fresh_config.get("custom_blocks", []) if b["id"] != del_cb_target["id"]]
                            fresh_config["custom_blocks"] = other_blocks
                            save_json(CONFIG_FILE, fresh_config)
                            st.session_state.admin_save_success = True; st.rerun()
                    else: st.info("현재 과목에 등록된 커스텀 블록이 없습니다.")

                st.markdown("---")
                st.markdown("#### 🔗 메인 화면 즐겨찾기/공지 링크 관리")
                st.info("💡 아래에서 등록한 링크들은 메인 화면에 버튼 형태로 학생들에게 즉시 노출됩니다.")
                current_dynamic_links = [l for l in fresh_config.get("dynamic_links", []) if l.get("subject", "전체 공지") == admin_view_subj]
                col_dl1, col_dl2 = st.columns(2)
                with col_dl1:
                    st.write("➕ **새로운 외부 링크 추가**")
                    with st.form("add_dynamic_link"):
                        existing_groups = list(dict.fromkeys([link["group"] for link in current_dynamic_links]))
                        if not existing_groups: existing_groups = ["👥 안내사항", "📚 참고 자료"]
                        new_dl_group = st.selectbox("어느 그룹(박스)에 넣을까요?", existing_groups + ["(새로운 그룹 직접 입력)"])
                        custom_dl_group = st.text_input("새로운 그룹 이름 (위에서 직접 입력을 선택한 경우)")
                        final_dl_group = custom_dl_group if new_dl_group == "(새로운 그룹 직접 입력)" and custom_dl_group else new_dl_group
                        new_dl_title = st.text_input("링크 제목 (예: 🔗 사전 설문조사 구글 폼)")
                        new_dl_url = st.text_input("URL 주소 (https://...)")
                        if st.form_submit_button("링크 추가하기", type="primary"):
                            if final_dl_group and new_dl_title and new_dl_url:
                                new_link = {"id": f"dl_{datetime.datetime.now().strftime('%d%H%M%S')}", "group": final_dl_group, "title": new_dl_title, "url": new_dl_url, "subject": admin_view_subj}
                                if "dynamic_links" not in fresh_config: fresh_config["dynamic_links"] = []
                                fresh_config["dynamic_links"].append(new_link)
                                save_json(CONFIG_FILE, fresh_config)
                                st.session_state.admin_save_success = True; st.rerun()
                            else: st.warning("모든 칸을 입력해주세요.")
                with col_dl2:
                    st.write("❌ **기존 외부 링크 삭제**")
                    if current_dynamic_links:
                        del_dl_target = st.selectbox("삭제할 링크를 선택하세요", current_dynamic_links, format_func=lambda x: f"[{x['group']}] {x['title']}")
                        if st.button("선택한 링크 삭제하기", type="primary"):
                            other_links = [l for l in fresh_config.get("dynamic_links", []) if l["id"] != del_dl_target["id"]]
                            fresh_config["dynamic_links"] = other_links
                            save_json(CONFIG_FILE, fresh_config)
                            st.session_state.admin_save_success = True; st.rerun()
                    else: st.info("현재 과목에 등록된 링크가 없습니다.")

                st.markdown("---")
                st.markdown("#### 📢 메인 화면 표 형식 공지사항 (자유 양식)")
                all_notices = fresh_config.get("notices", [])
                current_notices = [n for n in all_notices if n.get("subject", "전체 공지") == admin_view_subj]
                df_notices = pd.DataFrame(current_notices) if current_notices else pd.DataFrame([{"제목": "", "내용": ""}])
                edited_notices = st.data_editor(df_notices, num_rows="dynamic", use_container_width=True, hide_index=True)
                if st.button("표 형식 공지사항 저장 및 적용", type="primary"):
                    new_notices = [{"subject": admin_view_subj, "제목": str(row.get("제목", "")), "내용": str(row.get("내용", ""))} for row in edited_notices.to_dict('records') if str(row.get("제목", "")).strip() or str(row.get("내용", "")).strip()]
                    other_notices = [n for n in all_notices if n.get("subject", "전체 공지") != admin_view_subj]
                    fresh_config["notices"] = other_notices + new_notices
                    save_json(CONFIG_FILE, fresh_config)
                    st.session_state.admin_save_success = True; st.rerun()

                st.markdown("---")
                st.markdown("#### ⏰ 과목/반별 수행평가 수업 시간표 및 제출 기한 설정")
                st.info("💡 설정한 마감일과 주간 수업 시간 외에는 학생들의 접속과 입력이 완전히 차단됩니다.")
                
                if admin_view_subj in SUBJECTS:
                    subj_for_dl = admin_view_subj
                    acts_for_subj = fresh_config.get("subject_activities", {}).get(subj_for_dl, [])
                    if not acts_for_subj: st.warning("등록된 활동지가 없습니다.")
                    else:
                        selected_act_for_setting = st.selectbox("시간표를 설정할 수행평가 선택", acts_for_subj)
                        time_input_mode = st.radio("⏰ 시간 입력 방식 선택 (편한 방식을 먼저 선택한 후 아래 설정을 진행하세요)", ["🔘 드롭다운 선택 (10분 단위)", "🔘 직접 타이핑 (자유 입력)"], horizontal=True)
                        st.markdown("<br>", unsafe_allow_html=True)
                        
                        if "deadlines" not in fresh_config: fresh_config["deadlines"] = {}
                        new_act_deadlines = fresh_config["deadlines"].get(selected_act_for_setting, {})

                        with st.form(f"deadline_form_for_{selected_act_for_setting}"):
                            for c_group in CLASSES_MAP[subj_for_dl]:
                                with st.expander(f"🏫 {c_group} 시간표 설정", expanded=False):
                                    c_data = new_act_deadlines.get(c_group, {})
                                    c_final = c_data.get("final_dl", "2030-12-31 23:59")
                                    try: cf_dt = datetime.datetime.strptime(c_final, "%Y-%m-%d %H:%M")
                                    except: cf_dt = get_kst_now() + datetime.timedelta(days=30)

                                    col_f1, col_f2 = st.columns(2)
                                    f_date = col_f1.date_input(f"[{c_group}] 최종 제출 마감일", value=cf_dt.date(), key=f"f_date_{c_group}")
                                    
                                    if time_input_mode == "🔘 드롭다운 선택 (10분 단위)":
                                        f_time_str = col_f2.selectbox(f"[{c_group}] 최종 마감 시간", TIME_OPTIONS, index=get_time_index(cf_dt.strftime("%H:%M")), key=f"f_time_sel_{c_group}")
                                    else:
                                        f_time_str = col_f2.text_input(f"[{c_group}] 최종 마감 시간 (HH:MM 입력)", value=cf_dt.strftime("%H:%M"), key=f"f_time_txt_{c_group}")

                                    st.write("📌 주간 수업 시간표 (최대 3개)")
                                    c_slots = c_data.get("slots", [{"day": "선택안함", "period": "선택안함", "start": "00:00", "end": "00:00"}] * 3)
                                    while len(c_slots) < 3: c_slots.append({"day": "선택안함", "period": "선택안함", "start": "00:00", "end": "00:00"})

                                    updated_slots = []
                                    for i in range(3):
                                        sc1, sc2, sc3, sc4 = st.columns(4)
                                        day_opts = ["선택안함", "월", "화", "수", "목", "금"]
                                        period_opts = ["선택안함", "1교시", "2교시", "3교시", "4교시", "5교시", "6교시", "7교시", "8교시", "방과후"]
                                        
                                        cur_day = c_slots[i].get("day", "선택안함")
                                        day_idx = day_opts.index(cur_day) if cur_day in day_opts else 0
                                        cur_period = c_slots[i].get("period", "선택안함")
                                        period_idx = period_opts.index(cur_period) if cur_period in period_opts else 0

                                        st_t_str = c_slots[i].get("start", "09:00")
                                        en_t_str = c_slots[i].get("end", "09:50")

                                        slot_day = sc1.selectbox(f"수업 {i+1} 요일", day_opts, index=day_idx, key=f"day_{c_group}_{i}")
                                        slot_period = sc2.selectbox(f"수업 {i+1} 교시", period_opts, index=period_idx, key=f"period_{c_group}_{i}")
                                        
                                        if time_input_mode == "🔘 드롭다운 선택 (10분 단위)":
                                            slot_start_str = sc3.selectbox(f"수업 {i+1} 시작", TIME_OPTIONS, index=get_time_index(st_t_str), key=f"st_sel_{c_group}_{i}")
                                            slot_end_str = sc4.selectbox(f"수업 {i+1} 종료", TIME_OPTIONS, index=get_time_index(en_t_str), key=f"en_sel_{c_group}_{i}")
                                        else:
                                            slot_start_str = sc3.text_input(f"수업 {i+1} 시작 (HH:MM 입력)", value=st_t_str, key=f"st_txt_{c_group}_{i}")
                                            slot_end_str = sc4.text_input(f"수업 {i+1} 종료 (HH:MM 입력)", value=en_t_str, key=f"en_txt_{c_group}_{i}")

                                        updated_slots.append({"day": slot_day, "period": slot_period, "start": slot_start_str, "end": slot_end_str})

                                    new_act_deadlines[c_group] = {"final_dl": f"{f_date} {f_time_str}", "slots": updated_slots}
                            
                            if st.form_submit_button("이 수행평가의 반별 시간표 및 마감일 일괄 저장", type="primary"):
                                fresh_config["deadlines"][selected_act_for_setting] = new_act_deadlines
                                save_json(CONFIG_FILE, fresh_config)
                                st.session_state.admin_save_success = True; st.rerun()
                else:
                    st.info("⏰ 기한 설정은 왼쪽 '관리 및 미리보기 과목'에서 개별 과목을 선택해야만 편집 가능합니다.")

                st.markdown("---")
                st.markdown("#### 👨‍🏫 교사용 특강/수업 자료 업로드")
                with st.form("upload_mat"):
                    mat_title = st.text_input("자료 제목")
                    mat_link = st.text_input("외부 링크 URL (있는 경우)")
                    if st.form_submit_button("등록", type="primary"):
                        if mat_title and mat_link:
                            new_mat = {"id": f"mat_{datetime.datetime.now().strftime('%d%H%M%S')}", "title": mat_title, "type": "link", "content": mat_link, "subject": admin_view_subj}
                            if "materials" not in fresh_config: fresh_config["materials"] = []
                            fresh_config["materials"].append(new_mat)
                            save_json(CONFIG_FILE, fresh_config)
                            st.session_state.admin_save_success = True; st.rerun()

            with menu_tabs[1]:
                st.markdown("<h3 style='font-size: 24px; font-weight: 800; margin-top:20px;'>🗂️ 과목별 수행평가(활동지) 목록 관리</h3>", unsafe_allow_html=True)
                st.info("💡 각 과목에 새로운 수행평가를 코딩 없이 추가하거나 삭제할 수 있습니다. 추가된 수행평가는 학생 화면에 즉시 버튼으로 생성됩니다.")
                
                fresh_config = load_json(CONFIG_FILE, {})
                edit_subj = st.selectbox("수행평가를 관리할 과목 선택", SUBJECTS, key="edit_subj")
                acts_list = fresh_config.get("subject_activities", {}).get(edit_subj, [])
                
                st.write(f"**[{edit_subj}] 현재 등록된 수행평가 목록**")
                for a in acts_list: st.write(f"- {a}")
                
                col_add, col_del = st.columns(2)
                with col_add:
                    new_act_name = st.text_input("➕ 새로운 수행평가 제목 입력 (예: 수행평가 1 - 도시의 이해)")
                    if st.button("새 수행평가 추가하기", type="primary"):
                        if new_act_name and new_act_name not in acts_list:
                            fresh_config["subject_activities"][edit_subj].append(new_act_name)
                            fresh_config["custom_forms"][new_act_name] = [{"id": "q_1", "type": "textarea", "label": "수행평가 내용을 자유롭게 서술하세요."}]
                            save_json(CONFIG_FILE, fresh_config)
                            st.success("새 수행평가가 성공적으로 생성되었습니다!"); st.rerun()
                with col_del:
                    if acts_list:
                        del_act_name = st.selectbox("❌ 삭제할 수행평가 선택", ["선택"] + acts_list)
                        if del_act_name != "선택" and st.button("목록에서 영구 삭제하기", type="primary"):
                            fresh_config["subject_activities"][edit_subj].remove(del_act_name)
                            save_json(CONFIG_FILE, fresh_config)
                            st.success("수행평가 목록이 삭제되었습니다."); st.rerun()

                st.markdown("---")
                st.markdown("<h3 style='font-size: 24px; font-weight: 800; margin-top:20px;'>📝 수행평가 세부 문항 편집기 (폼 빌더)</h3>", unsafe_allow_html=True)
                st.info("💡 하드코딩된 기본 활동지 외에, 새로 직접 추가한 활동지의 질문(문항)을 자유롭게 무한정 구성할 수 있습니다.")
                
                custom_acts = [a for a in acts_list if a not in ACTIVITIES]
                if custom_acts:
                    edit_act = st.selectbox("문항을 편집할 수행평가 선택", custom_acts)
                    cur_form = fresh_config["custom_forms"].get(edit_act, [])
                    st.write("**현재 등록된 문항:**")
                    for q in cur_form: st.write(f"[{q['type']}] {q['label']}")
                    
                    col_q1, col_q2 = st.columns(2)
                    with col_q1:
                        new_q_type = st.radio("질문 유형", ["단답형 (한 줄 입력)", "서술형 (여러 줄 입력)"], horizontal=True)
                        new_q_label = st.text_input("새로운 질문 내용 입력")
                        if st.button("질문 추가하기", type="primary"):
                            if new_q_label:
                                new_id = f"q_{datetime.datetime.now().strftime('%H%M%S')}"
                                q_t = "text" if "단답" in new_q_type else "textarea"
                                fresh_config["custom_forms"][edit_act].append({"id": new_id, "type": q_t, "label": new_q_label})
                                save_json(CONFIG_FILE, fresh_config)
                                st.success("질문 추가 완료!"); st.rerun()
                    with col_q2:
                        if cur_form:
                            del_q = st.selectbox("삭제할 질문 선택", cur_form, format_func=lambda x: x['label'])
                            if st.button("선택한 질문 삭제하기", type="primary"):
                                fresh_config["custom_forms"][edit_act] = [q for q in cur_form if q['id'] != del_q['id']]
                                save_json(CONFIG_FILE, fresh_config)
                                st.success("질문 삭제 완료!"); st.rerun()
                else:
                    st.warning("현재 과목에 직접 추가한 커스텀 수행평가가 없습니다. 위에서 새 수행평가를 추가해보세요.")

            with menu_tabs[2]:
                all_users = load_json(USERS_FILE, {})
                pending_users = {k: v for k, v in all_users.items() if not v.get("approved", True) and v.get("role")=="학생"}
                approved_users = {k: v for k, v in all_users.items() if v.get("approved", True) and v.get("role")=="학생"}
                
                st.markdown("<h3 style='font-size: 24px; font-weight: 800; margin-top:20px;'>⏳ 가입 승인 대기 목록</h3>", unsafe_allow_html=True)
                if pending_users:
                    df_pending = pd.DataFrame([{"과목": v.get("subject", "-"), "반": v.get("class_group", "-"), "학번": v.get("id", "-"), "이름": v.get("name", "-")} for k, v in pending_users.items()])
                    st.dataframe(df_pending, use_container_width=True)
                    
                    col_app1, col_app2 = st.columns(2)
                    with col_app1:
                        approve_target = st.selectbox("승인할 학생 선택", ["선택"] + list(pending_users.keys()), format_func=lambda x: x if x=="선택" else f"[{pending_users[x].get('subject')}/{pending_users[x].get('class_group')}] {pending_users[x].get('name')} ({pending_users[x].get('id')})")
                        if approve_target != "선택" and st.button("✅ 선택한 학생 승인", type="primary"):
                            fresh_users = load_json(USERS_FILE, {})
                            if approve_target in fresh_users:
                                fresh_users[approve_target]["approved"] = True
                                save_json(USERS_FILE, fresh_users)
                            st.success("승인 완료!"); st.rerun()
                            
                    with col_app2:
                        st.write(" ")
                        st.write(" ")
                        if st.button("✅ 대기 중인 모든 학생 일괄 승인", type="primary"):
                            fresh_users = load_json(USERS_FILE, {})
                            for uid in pending_users.keys():
                                if uid in fresh_users: fresh_users[uid]["approved"] = True
                            save_json(USERS_FILE, fresh_users)
                            st.success("일괄 승인 완료!"); st.rerun()
                else: st.info("승인 대기 중인 학생이 없습니다.")
                
                st.markdown("---")
                st.markdown("<h3 style='font-size: 24px; font-weight: 800; margin-top:20px;'>✅ 기존 승인된 학생 목록 (조회 및 비밀번호 확인)</h3>", unsafe_allow_html=True)
                col1, col2 = st.columns(2)
                with col1:
                    filter_subj = st.selectbox("조회할 과목 선택", ["전체"] + SUBJECTS, key="manage_subj")
                with col2:
                    target_classes = ["전체"] + CLASSES_MAP.get(filter_subj, []) if filter_subj != "전체" else ["전체"] + [c for cl in CLASSES_MAP.values() for c in cl]
                    filter_class = st.selectbox("조회할 반 선택", target_classes, key="manage_class")
                
                filtered_approved = {k: v for k, v in approved_users.items() if (filter_subj == "전체" or v.get("subject", "").strip() == filter_subj.strip()) and (filter_class == "전체" or v.get("class_group", "").strip() == filter_class.strip())}
                df_users = pd.DataFrame([{"과목": v.get("subject", "-"), "반": v.get("class_group", "-"), "학번": v.get("id", "-"), "이름": v.get("name", "-"), "비밀번호": v.get("password", "-")} for k, v in filtered_approved.items()])
                st.dataframe(df_users, use_container_width=True)

                st.markdown("---")
                st.markdown("<h3 style='font-size: 24px; font-weight: 800; margin-top:20px;'>📝 학생 회원 정보(반/이름/학번) 수정</h3>", unsafe_allow_html=True)
                st.info("💡 학생이 가입 시 분반을 잘못 선택했거나 이름/학번 등에 오류가 있는 경우 즉시 수정할 수 있습니다.")
                
                search_edit = st.text_input("🔍 수정할 학생 검색 (이름, 과목, 반, 학번 입력)", key="search_edit")
                filtered_for_edit = filter_students(all_users, search_edit, approved_only=False)
                
                options_edit = ["선택"] + list(filtered_for_edit.keys())
                default_idx_edit = 1 if (search_edit.strip() and len(filtered_for_edit) > 0) else 0
                edit_target = st.selectbox("정보를 수정할 학생을 선택하세요", options_edit, index=default_idx_edit, format_func=lambda x: "선택" if x=="선택" else f"[{filtered_for_edit[x].get('subject')}/{filtered_for_edit[x].get('class_group')}] {filtered_for_edit[x].get('name')} ({filtered_for_edit[x].get('id')})", key="sel_edit")
                
                if edit_target != "선택":
                    target_info = filtered_for_edit[edit_target]
                    with st.form("edit_student_form"):
                        e_subj = st.selectbox("과목", SUBJECTS, index=SUBJECTS.index(target_info.get("subject")) if target_info.get("subject") in SUBJECTS else 0)
                        e_cls = st.selectbox("반", CLASSES_MAP.get(e_subj, []), index=CLASSES_MAP.get(e_subj, []).index(target_info.get("class_group")) if target_info.get("class_group") in CLASSES_MAP.get(e_subj, []) else 0)
                        e_id = st.text_input("학번", value=target_info.get("id", ""))
                        e_name = st.text_input("이름", value=target_info.get("name", ""))
                        
                        if st.form_submit_button("정보 수정 적용", type="primary"):
                            fresh_users = load_json(USERS_FILE, {})
                            fresh_data = load_json(DATA_FILE, {})
                            
                            new_key = f"{e_subj}_{e_cls}_{e_id}"
                            new_info = target_info.copy()
                            new_info.update({"subject": e_subj, "class_group": e_cls, "id": e_id, "name": e_name})
                            
                            if new_key != edit_target:
                                if new_key in fresh_users:
                                    st.error("❌ 변경하려는 과목/반/학번에 이미 다른 계정이 존재합니다.")
                                else:
                                    fresh_users[new_key] = new_info
                                    del fresh_users[edit_target]
                                    
                                    if edit_target in fresh_data:
                                        fresh_data[new_key] = fresh_data[edit_target]
                                        del fresh_data[edit_target]
                                        save_json(DATA_FILE, fresh_data)
                                        
                                    save_json(USERS_FILE, fresh_users)
                                    st.success("✅ 학생 정보가 성공적으로 변경되었습니다."); st.rerun()
                            else:
                                fresh_users[edit_target] = new_info
                                save_json(USERS_FILE, fresh_users)
                                st.success("✅ 학생 정보가 업데이트되었습니다."); st.rerun()

                st.markdown("<h3 style='font-size: 24px; font-weight: 800; margin-top:30px;'>⚙️ 개별 회원 권한 제어</h3>", unsafe_allow_html=True)
                col_ctrl1, col_ctrl2 = st.columns(2)
                
                with col_ctrl1:
                    st.markdown("<h4 style='color:#e74c3c;'>❌ 회원 강제 탈퇴(삭제)</h4>", unsafe_allow_html=True)
                    search_del = st.text_input("🔍 삭제할 회원 검색", key="search_del")
                    filtered_for_del = filter_students(all_users, search_del, approved_only=False)
                    
                    options_del = ["선택"] + list(filtered_for_del.keys())
                    default_idx_del = 1 if (search_del.strip() and len(filtered_for_del) > 0) else 0
                    
                    del_target = st.selectbox("삭제할 회원을 선택하세요", options_del, index=default_idx_del, format_func=lambda x: "선택" if x=="선택" else f"[{filtered_for_del[x].get('subject')}/{filtered_for_del[x].get('class_group')}] {filtered_for_del[x].get('name')} ({filtered_for_del[x].get('id')})", key="sel_del")
                    if del_target != "선택" and st.button("⚠️ 강제 탈퇴(삭제) 실행", type="primary"):
                        fresh_users = load_json(USERS_FILE, {})
                        if del_target in fresh_users: del fresh_users[del_target]
                        save_json(USERS_FILE, fresh_users)
                        st.success("삭제 완료"); st.rerun()
                        
                with col_ctrl2:
                    st.markdown("<h4 style='color:#f39c12;'>🔑 학생 비밀번호 강제 변경</h4>", unsafe_allow_html=True)
                    search_pw = st.text_input("🔍 비밀번호 변경할 회원 검색", key="search_pw")
                    filtered_for_pw = filter_students(all_users, search_pw, approved_only=False)
                    
                    options_pw = ["선택"] + list(filtered_for_pw.keys())
                    default_idx_pw = 1 if (search_pw.strip() and len(filtered_for_pw) > 0) else 0
                    
                    pw_target = st.selectbox("비밀번호를 변경할 회원을 선택하세요", options_pw, index=default_idx_pw, format_func=lambda x: "선택" if x=="선택" else f"[{filtered_for_pw[x].get('subject')}/{filtered_for_pw[x].get('class_group')}] {filtered_for_pw[x].get('name')} ({filtered_for_pw[x].get('id')})", key="sel_pw")
                    new_pw = st.text_input("새로운 비밀번호 입력", key="new_pw_input")
                    if pw_target != "선택" and st.button("비밀번호 변경 실행", type="primary"):
                        if new_pw:
                            fresh_users = load_json(USERS_FILE, {})
                            if pw_target in fresh_users: 
                                fresh_users[pw_target]["password"] = new_pw
                                save_json(USERS_FILE, fresh_users)
                                st.success("✅ 비밀번호 변경 완료!"); st.rerun()
                        else:
                            st.warning("새로운 비밀번호를 입력해주세요.")

            # --- 📥 탭 4: 학생 제출 자료 조회 및 관리 ---
            with menu_tabs[3]:
                col_t, col_b = st.columns([8, 2])
                with col_t: st.markdown("<h3 style='font-size: 26px; font-weight: 900;'>📥 학생 학습 활동 및 제출 자료 실시간 조회</h3>", unsafe_allow_html=True)
                with col_b: 
                    if st.button("🔄 실시간 새로고침", type="primary", use_container_width=True): st.rerun()
                
                st.markdown("---")
                st.markdown("<h4 style='font-size: 22px; font-weight: 800;'>🏫 과목 및 학급 필터링</h4>", unsafe_allow_html=True)
                
                col_filter1, col_filter2 = st.columns(2)
                with col_filter1:
                    view_subj = st.selectbox("조회할 과목 선택", SUBJECTS, key="view_subj_select")
                with col_filter2:
                    available_classes = CLASSES_MAP.get(view_subj, [])
                    view_class = st.radio("조회할 반 선택", ["전체 보기"] + available_classes, horizontal=True, key="view_class_select")
                
                view_mode = st.radio("조회 모드 선택", ["👤 특정 학생 실시간 집중 분석", "📅 항목별(수행평가) 전체 현황 (엑셀 다운로드)"], horizontal=True)
                st.markdown("---")

                all_users = load_json(USERS_FILE, {})
                learning_data = load_json(DATA_FILE, {})
                
                student_list = []
                for uid, info in all_users.items():
                    if info.get("role") == "학생" and info.get("approved", True):
                        s_subj = info.get("subject", "").strip()
                        s_class = info.get("class_group", "").strip()
                        if s_subj == view_subj.strip() and (view_class == "전체 보기" or s_class == view_class.strip()):
                            student_list.append(uid)
                
                if not student_list:
                    st.info("해당 조건에 등록된 학생이 없습니다. 가입 승인 대기 목록을 확인하거나, 상단 [🔄 실시간 새로고침] 버튼을 눌러보세요.")
                else:
                    if view_mode == "👤 특정 학생 실시간 집중 분석":
                        st.markdown("<h3 style='font-size: 24px; font-weight: 800;'>📦 전체 학생 포트폴리오 일괄 다운로드</h3>", unsafe_allow_html=True)
                        st.info("💡 현재 조회된 조건에 해당하는 모든 학생의 포트폴리오(HTML)를 하나의 압축 파일(ZIP)로 한 번에 다운로드합니다.")
                        
                        zip_buffer = io.BytesIO()
                        has_data = False
                        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                            for s_uid in student_list:
                                u_info_iter = all_users[s_uid]
                                acts_for_iter = load_json(CONFIG_FILE, {}).get("subject_activities", {}).get(view_subj, [])
                                
                                s_ans_dict = {}
                                for act in acts_for_iter:
                                    _, temp_ans = get_user_activity_data(s_uid, u_info_iter.get('id',''), view_subj, u_info_iter.get('class_group',''), act, learning_data)
                                    if temp_ans: s_ans_dict[act] = temp_ans
                                
                                if s_ans_dict:
                                    u_n = u_info_iter.get('name', '학생')
                                    u_c = u_info_iter.get('class_group', '')
                                    h_content = generate_portfolio_html(s_uid, u_info_iter, view_subj, load_json(CONFIG_FILE, {}), learning_data)
                                    file_n = f"{u_c}_{u_n}_포트폴리오.html"
                                    zip_file.writestr(file_n, h_content.encode('utf-8-sig'))
                                    has_data = True
                        
                        if has_data:
                            st.download_button(
                                label=f"📦 [{view_subj}] - [{view_class}] 학생 {len(student_list)}명 전체 포트폴리오 일괄 다운로드 (ZIP)",
                                data=zip_buffer.getvalue(),
                                file_name=f"{view_subj}_{view_class}_전체포트폴리오.zip",
                                mime="application/zip",
                                type="primary"
                            )
                        else:
                            st.warning("제출된 데이터가 없어 일괄 다운로드를 생성할 수 없습니다.")
                            
                        st.markdown("---")
                        st.markdown("<h3 style='font-size: 24px; font-weight: 800;'>👤 특정 학생 개별 조회 및 다운로드</h3>", unsafe_allow_html=True)
                        
                        search_student_tab4 = st.text_input("🔍 조회할 학생 검색 (이름, 과목, 반, 학번 입력)", key="search_student_tab4")
                        
                        filtered_student_list = []
                        for uid in student_list:
                            s_info = all_users[uid]
                            search_target = f"{s_info.get('subject','')} {s_info.get('class_group','')} {s_info.get('name','')} {s_info.get('id','')}"
                            if search_student_tab4.lower() in search_target.lower():
                                filtered_student_list.append(uid)
                                
                        def format_student_dropdown(x):
                            appr_str = "" if all_users[x].get("approved", True) else " (미승인)"
                            return f"[{all_users[x].get('class_group')}] {all_users[x].get('name')} ({all_users[x].get('id')}){appr_str}"
                            
                        options_student = ["선택"] + filtered_student_list
                        default_idx_student = 1 if (search_student_tab4.strip() and len(filtered_student_list) > 0) else 0

                        selected_student = st.selectbox("학생 선택", options_student, index=default_idx_student, format_func=lambda x: "선택" if x=="선택" else format_student_dropdown(x))
                        
                        if selected_student != "선택":
                            u_info_sel = all_users[selected_student]
                            u_name = u_info_sel.get('name', '학생')
                            u_class_selected = u_info_sel.get('class_group', '')
                            u_id_selected = u_info_sel.get('id', '')
                            
                            st.markdown(f"<h2 style='font-size: 28px; font-weight: 900;'>👀 <span style='color:#0056b3'>{u_name}</span> 학생의 실시간 활동 내역</h2>", unsafe_allow_html=True)
                            
                            acts_for_subj = load_json(CONFIG_FILE, {}).get("subject_activities", {}).get(view_subj, [])
                            
                            filter_act = st.selectbox("👀 화면에서 조회할 활동지 필터링", ["전체 활동지 보기"] + acts_for_subj)
                            st.markdown("---")
                            
                            has_any_act = False
                            
                            for act in acts_for_subj:
                                if filter_act != "전체 활동지 보기" and act != filter_act:
                                    continue
                                    
                                has_any_act = True
                                
                                # 📌 교사용 화면 학생 조회 폼
                                if act == ACT_3_1: render_activity1_3th(selected_student, u_info_sel, current_role)
                                elif act == ACT_3_2: render_activity2_3th(selected_student, u_info_sel, current_role)
                                elif act == ACT_3_3: render_activity3_3th(selected_student, u_info_sel, current_role)
                                elif act == ACT_2_1: render_activity1_2nd(selected_student, u_info_sel, current_role)
                                elif act == ACT_2_2: render_activity2_2nd(selected_student, u_info_sel, current_role)
                                elif act == ACT_2_3: render_activity3_2nd(selected_student, u_info_sel, current_role)
                                else: render_custom_activity(selected_student, u_info_sel, current_role, act, app_config)
                                
                                owner_key, ans = get_user_activity_data(selected_student, u_id_selected, view_subj, u_class_selected, act, learning_data)
                                if ans:
                                    st.write("")
                                    act_html = generate_activity_html(act, ans, u_name)
                                    st.download_button(label=f"📥 [{act}] 개별 결과물 다운로드 (웹문서)", data=act_html.encode('utf-8-sig'), file_name=f"{u_name}_{act}.html", mime="text/html", key=f"teach_dl_{selected_student}_{act}")
                                st.markdown("---")
                            
                            if not has_any_act:
                                st.warning("조건에 맞는 활동지가 없습니다.")
                            
                            if filter_act == "전체 활동지 보기":
                                html_content = generate_portfolio_html(selected_student, u_info_sel, view_subj, load_json(CONFIG_FILE, {}), learning_data)
                                st.download_button(label=f"📄 {u_name} 학생 전체 포트폴리오 일괄 다운로드 (웹문서)", data=html_content.encode('utf-8-sig'), file_name=f"{u_name}_{view_subj}_포트폴리오.html", mime="text/html", type="primary")

                    elif view_mode == "📅 항목별(수행평가) 전체 현황 (엑셀 다운로드)":
                        acts_for_subj = load_json(CONFIG_FILE, {}).get("subject_activities", {}).get(view_subj, [])
                        if not acts_for_subj:
                            st.warning("선택한 과목에 등록된 수행평가가 없습니다.")
                        else:
                            selected_view = st.selectbox("다운로드할 데이터 범주(수행평가)를 선택하세요", acts_for_subj)
                            st.info("💡 아래 화면은 렌더링된 모습이며, 하단의 버튼을 누르면 세로 형식으로 정리된 엑셀(CSV) 파일을 받을 수 있습니다.")
                            
                            csv_data = []
                            for s_uid in student_list:
                                u_info_csv = all_users[s_uid]
                                u_id = u_info_csv.get('id', '')
                                u_name = u_info_csv.get('name', '')
                                u_class = u_info_csv.get('class_group', '')
                                
                                owner_key, ans = get_user_activity_data(s_uid, u_id, view_subj, u_class, selected_view, learning_data)
                                
                                st.markdown(f"#### 👤 [{u_info_csv.get('subject', '')}] {u_class} - {u_name} ({u_id})")
                                csv_data.append([f"■ [{u_info_csv.get('subject', '')}] {u_class} - {u_name} ({u_id})", ""])
                                
                                if not ans:
                                    st.caption("제출된 활동 내용이 없습니다.")
                                    csv_data.append(["제출 여부", "미제출"])
                                    csv_data.append(["==================================================", ""])
                                    csv_data.append(["", ""])
                                    st.markdown("---")
                                    continue
                                
                                csv_data.extend(get_act_csv_rows(selected_view, ans, app_config))
                                
                                csv_data.append(["==================================================", ""])
                                csv_data.append(["", ""])
                                st.markdown("---")
                            
                            if csv_data:
                                df_csv = pd.DataFrame(csv_data)
                                st.download_button(f"📊 {selected_view[:6]} 엑셀 세로 양식 다운로드", data=df_csv.to_csv(index=False, header=False).encode('utf-8-sig'), file_name=f"{view_subj}_{view_class}_{selected_view[:6]}.csv", mime='text/csv', type="primary")
                            else:
                                st.info("해당 수행평가에 제출된 데이터가 없습니다.")

            # --- 💾 탭 5: 데이터 백업 및 복구 ---
            with menu_tabs[4]:
                st.markdown("<h3 style='font-size: 24px; font-weight: 800; margin-top:20px;'>💾 시스템 데이터베이스(DB) 백업 및 복구</h3>", unsafe_allow_html=True)
                st.error("🚨 **[주의]** 데이터 복구(업로드) 시 기존 데이터는 모두 지워지고 업로드한 파일로 완전히 덮어씌워집니다. 과거 자료 복원을 원하실 때만 신중하게 작업해 주세요!")

                col_bk1, col_bk2 = st.columns(2)
                with col_bk1:
                    st.markdown("#### 1️⃣ 현재 시스템 DB 다운로드 (백업)")
                    st.info("💡 코드 업데이트 전, 만약의 사태에 대비하여 반드시 아래 파일들을 다운로드하여 개인 PC에 안전하게 보관하세요.")

                    str_data = json.dumps(load_json(DATA_FILE, {}), ensure_ascii=False, indent=2)
                    st.download_button("📥 1. 학생 학습 데이터 백업 (learning_data.json)", data=str_data.encode('utf-8-sig'), file_name=f"learning_data_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json", use_container_width=True)

                    str_users = json.dumps(load_json(USERS_FILE, {}), ensure_ascii=False, indent=2)
                    st.download_button("📥 2. 회원 정보 데이터 백업 (users.json)", data=str_users.encode('utf-8-sig'), file_name=f"users_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json", use_container_width=True)

                    str_config = json.dumps(load_json(CONFIG_FILE, {}), ensure_ascii=False, indent=2)
                    st.download_button("📥 3. 시스템 설정 데이터 백업 (config.json)", data=str_config.encode('utf-8-sig'), file_name=f"config_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json", use_container_width=True)

                with col_bk2:
                    st.markdown("#### 2️⃣ 과거 시스템 DB 불러오기 (복구)")
                    st.info("💡 보관해둔 개별 json 파일을 아래에 각각 업로드하면 해당 영역만 100% 복구됩니다.")

                    st.write("📂 [1] 학생 학습 데이터 복구")
                    up_data = st.file_uploader("learning_data.json 업로드", type="json", key="up_data", label_visibility="collapsed")
                    if st.button("학생 학습 데이터 복구 실행", use_container_width=True):
                        if up_data:
                            try:
                                restored = json.load(up_data)
                                save_json(DATA_FILE, restored)
                                st.success("✅ 학습 데이터 복구 완료!")
                            except Exception: 
                                st.error("❌ 올바른 json 파일이 아닙니다.")

                    st.write("📂 [2] 회원 정보 데이터 복구")
                    up_users = st.file_uploader("users.json 업로드", type="json", key="up_users", label_visibility="collapsed")
                    if st.button("회원 정보 복구 실행", use_container_width=True):
                        if up_users:
                            try:
                                restored = json.load(up_users)
                                save_json(USERS_FILE, restored)
                                st.success("✅ 회원 정보 복구 완료!")
                            except Exception: 
                                st.error("❌ 올바른 json 파일이 아닙니다.")

                    st.write("📂 [3] 시스템 설정 데이터 복구")
                    up_config = st.file_uploader("config.json 업로드", type="json", key="up_config", label_visibility="collapsed")
                    if st.button("시스템 설정 복구 실행", use_container_width=True):
                        if up_config:
                            try:
                                restored = json.load(up_config)
                                save_json(CONFIG_FILE, restored)
                                st.success("✅ 시스템 설정 복구 완료!")
                            except Exception: 
                                st.error("❌ 올바른 json 파일이 아닙니다.")
