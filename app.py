import streamlit as st
import json
import os
import base64
import pandas as pd
import datetime
import threading
import io
import zipfile

# --- [1] 파일 경로 설정 및 상수 정의 ---
USERS_FILE = "users.json"
DATA_FILE = "learning_data.json"
CONFIG_FILE = "config.json"
UPLOAD_DIR = "uploads" 

os.makedirs(UPLOAD_DIR, exist_ok=True)

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

# 📌 수행평가 중심 활동지 3종 구성
ACTIVITIES = [
    "[활동지1] - 수행평가 1 (영상으로 떠나는 여행)",
    "[활동지2] - 수행평가 2 (나를 성장시킨 장소 지도 만들기)",
    "[활동지3] - 수행평가 3 (나의 세계관에 대해 알아가는 '여행')"
]

INFO_BOX = "<div style='background-color: #f0f4f8; padding: 15px; border-radius: 8px; font-size: 17px; font-weight: 600; color: #222; margin-bottom: 15px; border-left: 5px solid #0056b3; line-height: 1.5;'>{}</div>"

db_lock = threading.RLock()

# --- [토큰 및 페이지 제어 함수] ---
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
    st.rerun()

# --- [기간 설정(활성화) 확인 함수] ---
def check_active(act_name):
    config = load_json(CONFIG_FILE, {})
    deadlines = config.get("deadlines", {}).get(act_name, {})
    start_str = deadlines.get("start", "2020-01-01 00:00")
    end_str = deadlines.get("end", "2030-12-31 23:59")
    try:
        start_dt = datetime.datetime.strptime(start_str, "%Y-%m-%d %H:%M")
        end_dt = datetime.datetime.strptime(end_str, "%Y-%m-%d %H:%M")
        now = datetime.datetime.now()
        return start_dt <= now <= end_dt, start_str, end_str
    except:
        return True, start_str, end_str

# --- [2] 데이터 입출력 및 초기화 함수 ---
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

def init_system():
    with db_lock:
        users = load_json(USERS_FILE, {})
        users_changed = False
        
        for adm_id, adm_info in ADMIN_ACCOUNTS.items():
            if adm_id not in users or users[adm_id].get("password") != adm_info["pw"]:
                users[adm_id] = {
                    "id": adm_id, "password": adm_info["pw"], "name": adm_info["name"],
                    "role": "관리자", "subject": "전체", "class_group": "관리자", "approved": True
                }
                users_changed = True
        
        keys_to_delete = [k for k in users.keys() if users[k].get("role") == "관리자" and k not in ADMIN_ACCOUNTS]
        for k in keys_to_delete:
            del users[k]
            users_changed = True

        if users_changed: save_json(USERS_FILE, users)
        
        current_config = load_json(CONFIG_FILE, {})
        needs_update = False
        if "materials" not in current_config:
            current_config["materials"] = []
            needs_update = True
        if "deadlines" not in current_config:
            current_config["deadlines"] = {act: {"start": "2024-01-01 00:00", "end": "2030-12-31 23:59"} for act in ACTIVITIES}
            needs_update = True
        if "notices" not in current_config:
            current_config["notices"] = []
            needs_update = True
            
        for k in ["tabs", "pdfs", "questions"]:
            if k in current_config:
                del current_config[k]
                needs_update = True
        if needs_update: save_json(CONFIG_FILE, current_config)

# --- [엑셀 다운로드용 텍스트 변환 유틸] ---
def format_df_to_str(df_list, cols):
    if not df_list: return ""
    lines = []
    for row in df_list:
        lines.append(" | ".join([f"{c}: {row.get(c,'')}" for c in cols]))
    return "\n".join(lines)

# --- [공통 HTML 포트폴리오 생성기] ---
def generate_html_content(act_name, ans):
    html = ""
    if act_name == ACTIVITIES[0]:
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

    elif act_name == ACTIVITIES[1]:
        html += "<table>"
        html += f"<tr><th>1-1) 편안함을 주는 장소</th><td>{ans.get('q1_1','')}</td></tr>"
        html += f"<tr><th>1-2) 편안함을 주는 이유</th><td>{ans.get('q1_2','')}</td></tr>"
        html += f"<tr><th>2-1) 자신의 성격</th><td>{ans.get('q2_1','')}</td></tr>"
        html += f"<tr><th>2-2) 성격 형성에 영향을 준 장소</th><td>{ans.get('q2_2','')}</td></tr>"
        html += f"<tr><th>2-3) 그 이유</th><td>{ans.get('q2_3','')}</td></tr>"
        html += f"<tr><th>3-1) 자신의 장점</th><td>{ans.get('q3_1','')}</td></tr>"
        html += f"<tr><th>3-2) 장점 형성에 영향을 준 장소</th><td>{ans.get('q3_2','')}</td></tr>"
        html += f"<tr><th>3-3) 그 이유</th><td>{ans.get('q3_3','')}</td></tr>"
        html += f"<tr><th>4-1) 성장함에 영향을 준 장소</th><td>{ans.get('q4_1','')}</td></tr>"
        html += f"<tr><th>4-2) 어떤 면에서 영향을 주었는가</th><td>{ans.get('q4_2','')}</td></tr>"
        html += f"<tr><th>5-1) 지금 나의 목표</th><td>{ans.get('q5_1','')}</td></tr>"
        html += f"<tr><th>5-2) 목표 설정에 영향을 준 장소</th><td>{ans.get('q5_2','')}</td></tr>"
        html += f"<tr><th>6-1) 소중한 사람에게 소개하고 싶은 장소</th><td>{ans.get('q6_1','')}</td></tr>"
        html += f"<tr><th>6-2) 그 이유</th><td>{ans.get('q6_2','')}</td></tr>"
        html += f"<tr><th>7-1) 나만의 비밀 장소</th><td>{ans.get('q7_1','')}</td></tr>"
        html += f"<tr><th>7-2) 그 이유</th><td>{ans.get('q7_2','')}</td></tr>"
        html += f"<tr><th>8-1) 과거로 돌아간다면 가보고 싶은 장소</th><td>{ans.get('q8_1','')}</td></tr>"
        html += f"<tr><th>8-2) 그 이유</th><td>{ans.get('q8_2','')}</td></tr></table>"
        
    elif act_name == ACTIVITIES[2]:
        html += "<h3>1. 세계 인식 수준에 대한 확인</h3>"
        html += "<h4>1) 대륙별 관심도 및 지식 수준 체크</h4><table><tr><th>대륙</th><th>관심도</th><th>지식수준</th></tr>"
        for row in ans.get("s1_df", []): html += f"<tr><td>{row.get('대륙','')}</td><td>{row.get('관심도','')}</td><td>{row.get('지식수준','')}</td></tr>"
        html += "</table>"

        html += "<h4>2) 특정 국가에 대한 기억과 인상 분석</h4>"
        html += "<h5>[직접 경험]</h5><table><tr><th>여행해 본 국가</th><th>구체적인 기억 혹은 인상</th></tr>"
        for row in ans.get("direct_df", []): html += f"<tr><td>{row.get('여행해 본 국가','')}</td><td>{row.get('해당 국가에 대한 구체적인 기억 혹은 인상','')}</td></tr>"
        html += "</table>"
        html += f"<h5>[간접 경험]</h5><ul><li>즐겨 보는 외국 영화/드라마 나라 : {ans.get('ind1','')}</li>"
        html += f"<li>좋아하는 음악가/연예인 나라 : {ans.get('ind2','')}</li>"
        html += f"<li>자주 먹는 외국 음식 나라 : {ans.get('ind3','')}</li></ul>"

        html += "<h4>3) 꼭 가 보고 싶은 Top 5 국가와 그 이유</h4><table><tr><th>국가 혹은 지역</th><th>이유</th></tr>"
        for row in ans.get("top5_want", []): html += f"<tr><td>{row.get('국가 혹은 지역','')}</td><td>{row.get('이유','')}</td></tr>"
        html += "</table>"

        html += "<h4>4) 절대 가고 싫은 Top 5 국가와 그 이유</h4><table><tr><th>국가 혹은 지역</th><th>이유</th></tr>"
        for row in ans.get("top5_notwant", []): html += f"<tr><td>{row.get('국가 혹은 지역','')}</td><td>{row.get('이유','')}</td></tr>"
        html += "</table>"

        html += "<h3>2. 특정 대륙/국가에 대한 자신의 편견과 고정관념</h3>"
        html += "<h4>1) 국가별 한 단어 라벨링</h4><table><tr><th>가 보고 싶은 국가</th><th>한 단어 라벨</th><th>가고 싶지 않은 국가</th><th>한 단어 라벨(부정)</th></tr>"
        for row in ans.get("label_df", []): html += f"<tr><td>{row.get('가 보고 싶은 국가','')}</td><td>{row.get('한 단어 라벨','')}</td><td>{row.get('가고 싶지 않은 국가','')}</td><td>{row.get('한 단어 라벨(부정)','')}</td></tr>"
        html += "</table>"

        html += "<h4>2) 개인적으로 가장 강한 편견을 가진 국가</h4><table><tr><th>국가명</th><th>편견 내용</th><th>편견 형성 과정 혹은 이유</th></tr>"
        for row in ans.get("prej_df", []): html += f"<tr><td>{row.get('국가명','')}</td><td>{row.get('편견 내용','')}</td><td>{row.get('편견 형성 과정 혹은 이유','')}</td></tr>"
        html += "</table>"

        html += "<h4>3) 미디어와 교육의 영향으로 인한 인식 발견</h4><table>"
        html += f"<tr><th>뉴스에서 자주 접하는 국가들</th><td>{ans.get('media1_1','')}</td><th>그 나라들에 대한 이미지</th><td>{ans.get('media1_2','')}</td></tr>"
        html += f"<tr><th>영화/드라마에서 자주 접하는 국가들</th><td>{ans.get('media2_1','')}</td><th>그 나라들에 대한 이미지</th><td>{ans.get('media2_2','')}</td></tr>"
        html += f"<tr><th>학교에서 많이 배운 국가들</th><td>{ans.get('media3_1','')}</td><th>그 나라들에 대한 지식</th><td>{ans.get('media3_2','')}</td></tr></table>"

        html += "<h4>4) 부정확한 정보나 과장된 인식 발견 (사실과 다른 내용들)</h4><table><tr><th>국가명</th><th>잘못 알고 있었던 내용</th><th>실제 사실</th></tr>"
        for row in ans.get("fake_df", []): html += f"<tr><td>{row.get('국가명','')}</td><td>{row.get('잘못 알고 있었던 내용','')}</td><td>{row.get('실제 사실','')}</td></tr>"
        html += "</table>"

        html += "<h4>5) 우월감이나 차별 의식 점검</h4><table><tr><th>어떤 국가에 대해?</th><th>어떤 측면에서</th><th>그 이유</th></tr>"
        for row in ans.get("discrim_df", []): html += f"<tr><td>{row.get('어떤 국가에 대해?','')}</td><td>{row.get('어떤 측면에서','')}</td><td>{row.get('그 이유','')}</td></tr>"
        html += "</table>"

        html += "<h3>3. 포용적이고 균형잡힌 세계관을 위한 노력</h3>"
        html += "<h4>1) 편견을 바꾸고 싶은 국가</h4><table><tr><th>어떤 국가에 대해?</th><th>현재의 편견</th><th>올바른 정보를 찾기 위한 계획</th></tr>"
        for row in ans.get("change_df", []): html += f"<tr><td>{row.get('어떤 국가에 대해?','')}</td><td>{row.get('현재의 편견','')}</td><td>{row.get('올바른 정보를 찾기 위한 계획','')}</td></tr>"
        html += "</table>"

        html += "<h4>2) 가장 무관심했던 대륙 혹은 국가</h4><table><tr><th>선택 대륙/국가</th><th>무관심 이유</th><th>관심 확장을 위한 정보 수집 방법</th></tr>"
        for row in ans.get("ignore_df", []): html += f"<tr><td>{row.get('선택 대륙/국가','')}</td><td>{row.get('무관심 이유','')}</td><td>{row.get('관심 확장을 위한 정보 수집 방법','')}</td></tr>"
        html += "</table>"

        html += "<h4>3) 서구 중심적 시각에서 벗어나기</h4><table><tr><th>현재 가지고 있는 서구 중심적 시각</th><th>개선 방법</th></tr>"
        for row in ans.get("western_df", []): html += f"<tr><td>{row.get('현재 가지고 있는 서구 중심적 시각','')}</td><td>{row.get('개선 방법','')}</td></tr>"
        html += "</table>"

        html += "<h3>4. 목표로 하는 세계관</h3>"
        html += f"<p><b>▶ 어떤 사람이 되고 싶은가?</b></p><div class='content-box'>{ans.get('goal_1','')}</div>"
        html += f"<p><b>▶ 어떤 세계관을 갖고 싶은가?</b></p><div class='content-box'>{ans.get('goal_2','')}</div>"
        
    return html

def generate_portfolio_html(student_answers, u_name, u_class, view_subj):
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
    for act in ACTIVITIES:
        ans = student_answers.get(act, {})
        if not ans: continue
        html += f"<h2>▶ {act}</h2>"
        html += generate_html_content(act, ans)
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
    <div style="text-align: right; margin-bottom: 20px;"><b>이름:</b> {u_name}</div>
    <h2>▶ {act_name}</h2>
    """
    html += generate_html_content(act_name, ans)
    html += "</body></html>"
    return html

# --- [3] 수행평가 활동지 렌더링 함수들 ---
def render_activity1(user_key, u_name, current_role):
    category = ACTIVITIES[0]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    
    is_active, s_str, e_str = check_active(category)
    disabled_flag = (current_role == "학생" and not is_active)
    
    st.markdown("### ♣ 영상을 통한 여행 (Feat. 브이로그..)")
    st.markdown("---")
    
    if disabled_flag:
        st.error(f"⏳ 현재는 입력 및 제출 기간이 아닙니다. (설정된 제출 기간: {s_str} ~ {e_str})")
    
    st.markdown("#### 1. 자신이 선택한 영상에 대한 첫번째 질문")
    a1_1 = st.text_input("1. 영상의 제목", value=ans.get("a1_1", ""), disabled=disabled_flag)
    a1_2 = st.text_input("2. 영상에 등장하는 국가 혹은 지역", value=ans.get("a1_2", ""), disabled=disabled_flag)
    a1_3 = st.text_area("3. 해당 영상을 선택하게 된 이유 (자세히 작성해 봅시다.)", value=ans.get("a1_3", ""), disabled=disabled_flag)
    
    st.markdown("---")
    st.markdown("#### 2. 자신이 선택한 영상에 대한 두 번째 질문")
    a2_1 = st.text_area("1. 첫 느낌 (영상의 제목 혹은 첫 장면을 접했을 때 느낌)", value=ans.get("a2_1", ""), disabled=disabled_flag)
    
    st.markdown("**2. 영상의 내용 중 가장 인상적이었던 장소 혹은 공간을 한 곳 선택해 본다면?**")
    a2_2_1 = st.text_input("▶ 인상적이었던 장소 혹은 공간:", value=ans.get("a2_2_1", ""), disabled=disabled_flag)
    a2_2_2 = st.text_area("▶ 이유:", value=ans.get("a2_2_2", ""), disabled=disabled_flag)
    
    st.markdown("**3. 만일 이 영상을 누군가에게 추천해 준다면 누구에게 추천해 주고 싶은지, 그 이유는 무엇인지?**")
    a2_3_1 = st.text_input("▶ 누구에게 추천:", value=ans.get("a2_3_1", ""), disabled=disabled_flag)
    a2_3_2 = st.text_area("▶ 추천하는 이유:", value=ans.get("a2_3_2", ""), disabled=disabled_flag)
    
    a2_4 = st.text_area("4. 영상에 대한 나만의 감상평 (인상적이었던 부분, 좋았던 부분, 아쉬웠던 부분)", value=ans.get("a2_4", ""), disabled=disabled_flag)
    
    st.markdown("---")
    st.markdown("#### 5. 만일 내가 영상 속 지역을 배경으로 영상을 찍는다면?")
    a3_1 = st.text_input("1) 영상의 제목:", value=ans.get("a3_1", ""), disabled=disabled_flag)
    a3_2 = st.text_input("2) 영상의 주요 컨셉 혹은 느낌:", value=ans.get("a3_2", ""), disabled=disabled_flag)
    a3_3 = st.text_input("3) 누구와 함께 가고 싶은가? (혼자 혹은 누군가와 함께?):", value=ans.get("a3_3", ""), disabled=disabled_flag)
    a3_4 = st.text_area("4) 그 이유는?:", value=ans.get("a3_4", ""), disabled=disabled_flag)
    a3_5 = st.text_input("5) 그곳에서 가장 해 보고 싶은 것이 있다면?:", value=ans.get("a3_5", ""), disabled=disabled_flag)
    a3_6 = st.text_area("6) 그 이유는?:", value=ans.get("a3_6", ""), key="a3_6", disabled=disabled_flag)
    a3_7 = st.text_input("7) 영상에 꼭 넣고 싶은 장소 혹은 공간:", value=ans.get("a3_7", ""), disabled=disabled_flag)
    a3_8 = st.text_area("8) 그 이유는?:", value=ans.get("a3_8", ""), key="a3_8", disabled=disabled_flag)
    a3_9 = st.text_area("9) 만일 내가 썸네일 영상을 만든다면?:", value=ans.get("a3_9", ""), disabled=disabled_flag)
    a3_10 = st.text_input("10) 영상에 어울리는 BGM을 하나 선택한다면 어떤 곡으로?:", value=ans.get("a3_10", ""), disabled=disabled_flag)
    a3_11 = st.text_area("11) 그 이유는?:", value=ans.get("a3_11", ""), key="a3_11", disabled=disabled_flag)
    
    if current_role == "학생" and not disabled_flag:
        if st.button("저장하기", type="primary", key="save_act1"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "a1_1": a1_1, "a1_2": a1_2, "a1_3": a1_3, "a2_1": a2_1, "a2_2_1": a2_2_1, "a2_2_2": a2_2_2,
                "a2_3_1": a2_3_1, "a2_3_2": a2_3_2, "a2_4": a2_4, "a3_1": a3_1, "a3_2": a3_2, "a3_3": a3_3,
                "a3_4": a3_4, "a3_5": a3_5, "a3_6": a3_6, "a3_7": a3_7, "a3_8": a3_8, "a3_9": a3_9,
                "a3_10": a3_10, "a3_11": a3_11
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data)
            ans = new_ans
            
            st.balloons()
            st.markdown("""
            <div style='text-align: center; padding: 25px; background-color: #e8f5e9; color: #2e7d32; border-radius: 15px; border: 3px solid #4CAF50; margin: 20px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
                <h2 style='margin:0 0 10px 0; color: #2e7d32;'>🎉 화면 저장이 완료되었습니다!</h2>
                <p style='margin:0; font-size: 18px; font-weight: bold;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p>
            </div>
            """, unsafe_allow_html=True)

    if current_role == "관리자":
        st.info("💡 교사/관리자 모드: 학생들에게 활동지 내용을 설명하기 위한 미리보기 화면입니다.")
        
    if ans:
        st.markdown("---")
        html_data = generate_activity_html(category, ans, u_name)
        st.download_button(f"📥 수행평가 1 다운로드 (웹문서)", data=html_data.encode('utf-8-sig'), file_name=f"{u_name}_수행평가1.html", mime="text/html")


def render_activity2(user_key, u_name, current_role):
    category = ACTIVITIES[1]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    
    is_active, s_str, e_str = check_active(category)
    disabled_flag = (current_role == "학생" and not is_active)
    
    st.markdown("## 2026. 신선여고 3학년 여행지리")
    st.markdown("### '나를 성장시킨, 나에게 특별한 의미가 있는 장소 지도 만들기'")
    st.info("한 사람의 살아온 과정은 '장소'의 영향을 받기 마련입니다. 우리의 삶이 이어지는 '장소'는 개인의 느낌과 의미 부여에 따라 저마다 다른 감정을 느낍니다. 이를 지리에서는 '장소감(Sense of place)' 이라고 합니다.\n\n19년 동안의 인생을 살아오면서 지금의 내가 있기까지 성장의 경험을 했던 혹은 나에게 특별한 의미가 있는 장소들을 떠올려 봅시다. 그리고 지금의 내가 있기까지의 기억이 남는 장소를 선정해서 '과거로 떠나는 나를 성장시킨 장소 지도'를 만들어 봅시다. 소중했던 사람들과의 좋은 기억과 추억이 남아 있는 장소로 한 번 떠나봅시다.")
    
    if disabled_flag:
        st.error(f"⏳ 현재는 입력 및 제출 기간이 아닙니다. (설정된 제출 기간: {s_str} ~ {e_str})")
        
    st.markdown("---")
    q1_1 = st.text_input("1-1) 나에게 편안함을 주는 장소(공간)이/가 있는가?", value=ans.get("q1_1", ""), disabled=disabled_flag)
    q1_2 = st.text_area("1-2) 그 장소(공간)이/가 어떤 면에서 나에게 편안함을 주는 것 같은가?", value=ans.get("q1_2", ""), disabled=disabled_flag)
    
    st.markdown("---")
    q2_1 = st.text_input("2-1) 자신이 생각하기에 자신의 성격은?", value=ans.get("q2_1", ""), disabled=disabled_flag)
    q2_2 = st.text_input("2-2) 자신의 성격 형성에 있어 영향을 준 장소(공간)이/가 있는가?", value=ans.get("q2_2", ""), disabled=disabled_flag)
    q2_3 = st.text_area("2-3) 만약 그런 장소(공간)이/가 있다면 무엇 때문인가?", value=ans.get("q2_3", ""), disabled=disabled_flag)
    
    st.markdown("---")
    q3_1 = st.text_input("3-1) 자신이 생각하기에 자신의 장점은?", value=ans.get("q3_1", ""), disabled=disabled_flag)
    q3_2 = st.text_input("3-2) 자신의 장점 형성에 있어 영향을 준 장소(공간)이/가 있는가?", value=ans.get("q3_2", ""), disabled=disabled_flag)
    q3_3 = st.text_area("3-3) 만약 그런 장소(공간)이/가 있다면 무엇 때문인가?", value=ans.get("q3_3", ""), disabled=disabled_flag)
    
    st.markdown("---")
    q4_1 = st.text_input("4-1) 내가 성장함에 있어 영향을 준 장소(공간)이/가 있는가?", value=ans.get("q4_1", ""), disabled=disabled_flag)
    q4_2 = st.text_area("4-2) 그런 장소(공간)이/가 있다면 어떤 면에서 영향을 준 것 같은가?", value=ans.get("q4_2", ""), disabled=disabled_flag)
    
    st.markdown("---")
    q5_1 = st.text_input("5-1) 지금 나의 목표는 무엇인가?", value=ans.get("q5_1", ""), disabled=disabled_flag)
    q5_2 = st.text_area("5-2) 그런 목표를 설정함에 있어 영향을 준 장소(공간)이/가 있는가?", value=ans.get("q5_2", ""), disabled=disabled_flag)
    
    st.markdown("---")
    q6_1 = st.text_input("6-1) 훗날 소중한 사람에게 소개해 주고 싶은 장소(공간)이/가 있는가?", value=ans.get("q6_1", ""), disabled=disabled_flag)
    q6_2 = st.text_area("6-2) 만약 그런 장소(공간)이/가 있다면 무엇 때문인가?", value=ans.get("q6_2", ""), disabled=disabled_flag)
    
    st.markdown("---")
    q7_1 = st.text_input("7-1) 나만의 비밀 장소(공간)이/가 있는가?", value=ans.get("q7_1", ""), disabled=disabled_flag)
    q7_2 = st.text_area("7-2) 만약 그런 장소(공간)이/가 있다면 무엇 때문인가?", value=ans.get("q7_2", ""), disabled=disabled_flag)
    
    st.markdown("---")
    q8_1 = st.text_input("8-1) 시간을 돌려 과거로 돌아갈 수 있다면 다시 가 보고 싶은 장소(공간)이/가 있는가?", value=ans.get("q8_1", ""), disabled=disabled_flag)
    q8_2 = st.text_area("8-2) 장소(공간)로/으로 다시 가 보고 싶은 이유는 무엇 때문인가?", value=ans.get("q8_2", ""), disabled=disabled_flag)
    
    if current_role == "학생" and not disabled_flag:
        if st.button("저장하기", type="primary", key="save_act2"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "q1_1": q1_1, "q1_2": q1_2, "q2_1": q2_1, "q2_2": q2_2, "q2_3": q2_3,
                "q3_1": q3_1, "q3_2": q3_2, "q3_3": q3_3, "q4_1": q4_1, "q4_2": q4_2,
                "q5_1": q5_1, "q5_2": q5_2, "q6_1": q6_1, "q6_2": q6_2, "q7_1": q7_1,
                "q7_2": q7_2, "q8_1": q8_1, "q8_2": q8_2
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data)
            ans = new_ans
            
            st.balloons()
            st.markdown("""
            <div style='text-align: center; padding: 25px; background-color: #e8f5e9; color: #2e7d32; border-radius: 15px; border: 3px solid #4CAF50; margin: 20px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
                <h2 style='margin:0 0 10px 0; color: #2e7d32;'>🎉 화면 저장이 완료되었습니다!</h2>
                <p style='margin:0; font-size: 18px; font-weight: bold;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p>
            </div>
            """, unsafe_allow_html=True)
            
    if current_role == "관리자":
        st.info("💡 교사/관리자 모드: 학생들에게 활동지 내용을 설명하기 위한 미리보기 화면입니다.")

    if ans:
        st.markdown("---")
        html_data = generate_activity_html(category, ans, u_name)
        st.download_button(f"📥 수행평가 2 다운로드 (웹문서)", data=html_data.encode('utf-8-sig'), file_name=f"{u_name}_수행평가2.html", mime="text/html")


def render_activity3(user_key, u_name, current_role):
    category = ACTIVITIES[2]
    ans = load_json(DATA_FILE, {}).get(user_key, {}).get(category, {})
    
    is_active, s_str, e_str = check_active(category)
    disabled_flag = (current_role == "학생" and not is_active)
    
    st.markdown("### ♣ 나의 세계관에 대해 알아가는 '여행'")
    st.markdown("---")

    if disabled_flag:
        st.error(f"⏳ 현재는 입력 및 제출 기간이 아닙니다. (설정된 제출 기간: {s_str} ~ {e_str})")

    st.markdown("#### 1. 세계 인식 수준에 대한 확인")
    st.markdown("**1) 대륙별 관심도 및 지식 수준 체크**")
    continents = ["아시아", "유럽", "북아메리카", "남아메리카", "아프리카", "오세아니아"]
    levels = ["선택", "매우높음", "높음", "보통", "낮음", "매우낮음"]
    
    s1_dis = True if disabled_flag else ["대륙"]
    default_s1_df = pd.DataFrame([{"대륙": c, "관심도": "선택", "지식수준": "선택"} for c in continents])
    s1_df = pd.DataFrame(ans.get("s1_df", default_s1_df.to_dict('records')))
    edited_s1_df = st.data_editor(
        s1_df,
        column_config={
            "관심도": st.column_config.SelectboxColumn("관심도", options=levels, required=True),
            "지식수준": st.column_config.SelectboxColumn("지식수준", options=levels, required=True)
        },
        disabled=s1_dis, hide_index=True, use_container_width=True
    )

    st.markdown("**2) 특정 국가에 대한 기억과 인상에 대한 분석**")
    st.caption("[직접 경험]")
    default_direct_df = pd.DataFrame([{"여행해 본 국가": "", "해당 국가에 대한 구체적인 기억 혹은 인상": ""} for _ in range(3)])
    direct_df = pd.DataFrame(ans.get("direct_df", default_direct_df.to_dict('records')))
    edited_direct_df = st.data_editor(direct_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.caption("[간접 경험]")
    ind1 = st.text_input("즐겨 보는 외국 영화/드라마는 어느 나라 작품?", value=ans.get("ind1", ""), disabled=disabled_flag)
    ind2 = st.text_input("좋아하는 음악가나 연예인이 있다면 어느 나라?", value=ans.get("ind2", ""), disabled=disabled_flag)
    ind3 = st.text_input("자주 먹는 외국 음식이 있다면 어느 나라?", value=ans.get("ind3", ""), disabled=disabled_flag)

    st.markdown("**3) 꼭 가 보고 싶은 Top 5 국가와 그 이유**")
    default_top5_want = pd.DataFrame([{"국가 혹은 지역": "", "이유": ""} for _ in range(5)])
    top5_want = pd.DataFrame(ans.get("top5_want", default_top5_want.to_dict('records')))
    edited_top5_want = st.data_editor(top5_want, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.markdown("**4) 절대 가고 싫은 Top 5 국가와 그 이유**")
    default_top5_notwant = pd.DataFrame([{"국가 혹은 지역": "", "이유": ""} for _ in range(5)])
    top5_notwant = pd.DataFrame(ans.get("top5_notwant", default_top5_notwant.to_dict('records')))
    edited_top5_notwant = st.data_editor(top5_notwant, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.markdown("---")
    st.markdown("#### 2. 특정 대륙/국가에 대한 자신의 편견과 고정관념")
    st.markdown("**1) 국가별 한 단어 라벨링**")
    default_label = pd.DataFrame([{"가 보고 싶은 국가": "", "한 단어 라벨": "", "가고 싶지 않은 국가": "", "한 단어 라벨(부정)": ""} for _ in range(3)])
    label_df = pd.DataFrame(ans.get("label_df", default_label.to_dict('records')))
    edited_label_df = st.data_editor(label_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.markdown("**2) 개인적으로 가장 강한 편견을 가진 국가**")
    default_prej = pd.DataFrame([{"국가명": "", "편견 내용": "", "편견 형성 과정 혹은 이유": ""} for _ in range(2)])
    prej_df = pd.DataFrame(ans.get("prej_df", default_prej.to_dict('records')))
    edited_prej_df = st.data_editor(prej_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.markdown("**3) 미디어와 교육의 영향으로 인한 인식 발견**")
    col1, col2 = st.columns(2)
    media1_1 = col1.text_area("뉴스에서 자주 접하는 국가들", value=ans.get("media1_1", ""), height=80, disabled=disabled_flag)
    media1_2 = col2.text_area("그 나라들에 대한 이미지 (뉴스)", value=ans.get("media1_2", ""), height=80, disabled=disabled_flag)
    media2_1 = col1.text_area("영화/드라마에서 자주 접하는 국가들", value=ans.get("media2_1", ""), height=80, disabled=disabled_flag)
    media2_2 = col2.text_area("그 나라들에 대한 이미지 (영화/드라마)", value=ans.get("media2_2", ""), height=80, disabled=disabled_flag)
    media3_1 = col1.text_area("학교에서 많이 배운 국가들", value=ans.get("media3_1", ""), height=80, disabled=disabled_flag)
    media3_2 = col2.text_area("그 나라들에 대한 지식", value=ans.get("media3_2", ""), height=80, disabled=disabled_flag)

    st.markdown("**4) 부정확한 정보나 과장된 인식 발견 (사실과 다른 내용들)**")
    default_fake = pd.DataFrame([{"국가명": "", "잘못 알고 있었던 내용": "", "실제 사실": ""} for _ in range(3)])
    fake_df = pd.DataFrame(ans.get("fake_df", default_fake.to_dict('records')))
    edited_fake_df = st.data_editor(fake_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.markdown("**5) 우월감이나 차별 의식 점검**")
    default_discrim = pd.DataFrame([{"어떤 국가에 대해?": "", "어떤 측면에서": "", "그 이유": ""} for _ in range(2)])
    discrim_df = pd.DataFrame(ans.get("discrim_df", default_discrim.to_dict('records')))
    edited_discrim_df = st.data_editor(discrim_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.markdown("---")
    st.markdown("#### 3. 포용적이고 균형잡힌 세계관을 위한 노력")
    st.markdown("**1) 편견을 바꾸고 싶은 국가**")
    default_change = pd.DataFrame([{"어떤 국가에 대해?": "", "현재의 편견": "", "올바른 정보를 찾기 위한 계획": ""} for _ in range(2)])
    change_df = pd.DataFrame(ans.get("change_df", default_change.to_dict('records')))
    edited_change_df = st.data_editor(change_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.markdown("**2) 가장 무관심했던 대륙 혹은 국가**")
    default_ignore = pd.DataFrame([{"선택 대륙/국가": "", "무관심 이유": "", "관심 확장을 위한 정보 수집 방법": ""} for _ in range(2)])
    ignore_df = pd.DataFrame(ans.get("ignore_df", default_ignore.to_dict('records')))
    edited_ignore_df = st.data_editor(ignore_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.markdown("**3) 서구 중심적 시각에서 벗어나기**")
    default_western = pd.DataFrame([{"현재 가지고 있는 서구 중심적 시각": "", "개선 방법": ""} for _ in range(2)])
    western_df = pd.DataFrame(ans.get("western_df", default_western.to_dict('records')))
    edited_western_df = st.data_editor(western_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag)

    st.markdown("---")
    st.markdown("#### 4. 목표로 하는 세계관")
    goal_1 = st.text_area("▶ 어떤 사람이 되고 싶은가?", value=ans.get("goal_1", ""), height=100, disabled=disabled_flag)
    goal_2 = st.text_area("▶ 어떤 세계관을 갖고 싶은가?", value=ans.get("goal_2", ""), height=100, disabled=disabled_flag)
    
    if current_role == "학생" and not disabled_flag:
        if st.button("저장하기", type="primary", key="save_act3"):
            current_data = load_json(DATA_FILE, {}) 
            if user_key not in current_data: current_data[user_key] = {}
            new_ans = {
                "s1_df": edited_s1_df.to_dict('records'),
                "direct_df": edited_direct_df.to_dict('records'),
                "ind1": ind1, "ind2": ind2, "ind3": ind3,
                "top5_want": edited_top5_want.to_dict('records'),
                "top5_notwant": edited_top5_notwant.to_dict('records'),
                "label_df": edited_label_df.to_dict('records'),
                "prej_df": edited_prej_df.to_dict('records'),
                "media1_1": media1_1, "media1_2": media1_2,
                "media2_1": media2_1, "media2_2": media2_2,
                "media3_1": media3_1, "media3_2": media3_2,
                "fake_df": edited_fake_df.to_dict('records'),
                "discrim_df": edited_discrim_df.to_dict('records'),
                "change_df": edited_change_df.to_dict('records'),
                "ignore_df": edited_ignore_df.to_dict('records'),
                "western_df": edited_western_df.to_dict('records'),
                "goal_1": goal_1, "goal_2": goal_2
            }
            current_data[user_key][category] = new_ans
            save_json(DATA_FILE, current_data)
            ans = new_ans
            
            st.balloons()
            st.markdown("""
            <div style='text-align: center; padding: 25px; background-color: #e8f5e9; color: #2e7d32; border-radius: 15px; border: 3px solid #4CAF50; margin: 20px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
                <h2 style='margin:0 0 10px 0; color: #2e7d32;'>🎉 화면 저장이 완료되었습니다!</h2>
                <p style='margin:0; font-size: 18px; font-weight: bold;'>입력하신 내용이 데이터베이스에 안전하게 저장되었습니다.</p>
            </div>
            """, unsafe_allow_html=True)
            
    if current_role == "관리자":
        st.info("💡 교사/관리자 모드: 학생들에게 활동지 내용을 설명하기 위한 미리보기 화면입니다.")

    if ans:
        st.markdown("---")
        html_data = generate_activity_html(category, ans, u_name)
        st.download_button(f"📥 수행평가 3 다운로드 (웹문서)", data=html_data.encode('utf-8-sig'), file_name=f"{u_name}_수행평가3.html", mime="text/html")

# --- 메인 공지사항 렌더링 ---
def render_class_overview(current_role, u_info):
    st.subheader(f"🎯 [{u_info.get('subject', '전체')}] 수행평가 및 활동 모듈")
    st.markdown("---")
    
    app_config = load_json(CONFIG_FILE, {})
    
    # 📌 (해결 1) 공지사항 즉각 렌더링 로직 위치 복원
    notices = app_config.get("notices", [])
    if notices:
        st.markdown("### 📢 알림 및 공지사항")
        for notice in notices:
            t = notice.get("제목", "").strip()
            c = notice.get("내용", "").strip()
            if t or c:
                st.info(f"**{t}**\n\n{c}")
        st.markdown("---")

    materials = app_config.get("materials", [])
    if materials:
        st.subheader("👨‍🏫 수업 공지 및 자료실")
        for mat in materials:
            if mat.get("subject", "전체") in ["전체", u_info.get('subject', '')]:
                if mat["type"] == "link": st.markdown(f"🔗 **[{mat['title']}]({mat['content']})**")
                elif mat["type"] == "file" and os.path.exists(mat["content"]):
                    with open(mat["content"], "rb") as f: 
                        st.download_button(f"📥 {mat['title']} ({mat['filename']}) 다운로드", f, file_name=mat['filename'], key=f"mat_dl_{mat['id']}")
        st.markdown("---")

    st.markdown("### 📝 학년별 수행평가 목록")
    st.caption("아래 버튼을 눌러 해당 수행평가 작성 화면으로 이동하세요.")
    
    cols = st.columns(3)
    for idx, act in enumerate(ACTIVITIES):
        with cols[idx]:
            if st.button(f"📄 {act}", use_container_width=True):
                change_page(act)

# --- [4] 메인 화면 세팅 및 CSS ---
st.set_page_config(page_title="수업 및 활동 어시스트 프로그램", layout="wide")

st.markdown("""
<style>
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stSelectbox label p,
[data-testid="stSidebar"] .stTextInput label p,
[data-testid="stSidebar"] .stRadio label p,
[data-testid="stSidebar"] div[data-baseweb="radio"] div {
    font-size: 20px !important;
    font-weight: 900 !important;
    color: #111111 !important;
}
[data-testid="stSidebar"] input, [data-testid="stSidebar"] div[data-baseweb="select"] {
    font-size: 18px !important;
    font-weight: 700 !important;
}
[data-testid="stFormSubmitButton"] button, button[kind="primary"] {
    background-color: #FF4B4B !important; 
    color: white !important; 
    font-size: 22px !important; 
    font-weight: 900 !important;
    padding: 12px !important; 
    border-radius: 8px !important; 
    border: none !important; 
    min-height: 50px !important; 
    width: 100% !important;
}
button[kind="primary"] p {
    font-size: 22px !important; 
    font-weight: 900 !important;
}
button[kind="secondary"] {
    background-color: #0056b3 !important; 
    color: white !important; 
    font-size: 22px !important; 
    font-weight: 900 !important;
    padding: 12px !important; 
    border-radius: 8px !important; 
    border: none !important; 
    min-height: 50px !important;
    width: 100% !important;
}
button[kind="secondary"] p {
    color: white !important;
    font-size: 22px !important; 
    font-weight: 900 !important;
}
.stMarkdown p { font-size: 16px !important; color: #222222 !important; font-weight: 600 !important; line-height: 1.6 !important; }
[data-testid="stDataFrame"] { border: 2px solid #333 !important; border-radius: 5px; }
table th { background-color: #f0f2f6 !important; font-size: 16px !important; font-weight: 900 !important; text-align:center !important;}
table td { font-size: 15px !important; font-weight: 600 !important; }
</style>
""", unsafe_allow_html=True)

init_system()

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
            st.session_state.user_info = {
                "user_key": user_key, "id": user_key, "name": ADMIN_ACCOUNTS[user_key]["name"], 
                "role": "관리자", "subject": "전체", "class_group": "관리자"
            }
        elif user_key in users and users[user_key].get("approved", True):
            st.session_state.logged_in = True
            st.session_state.user_info = users[user_key]
            st.session_state.user_info["user_key"] = user_key

if "current_page" in st.query_params:
    st.session_state.current_page = st.query_params["current_page"]
elif "current_page" not in st.session_state: 
    st.session_state.current_page = "main"

st.sidebar.title("🔒 인증 센터")

if st.session_state.logged_in:
    u_info = st.session_state.user_info
    
    if u_info['role'] == "관리자":
        sidebar_html = f"<div style='background-color:#e8f4f8; padding:10px; border-radius:5px; margin-bottom:15px; line-height:1.4;'><div style='font-size:15px; font-weight:bold; color:#0056b3; margin-bottom:3px;'>🟢 {u_info['name']} 님 로그인 중</div><div style='font-size:14px; color:#333; margin-bottom:2px;'>📘 과목: {u_info.get('subject', '전체')}</div><div style='font-size:14px; color:#333;'>🛡️ 권한: {u_info['role']}</div></div>"
    else:
        sidebar_html = f"<div style='background-color:#e8f4f8; padding:10px; border-radius:5px; margin-bottom:15px; line-height:1.4;'><div style='font-size:15px; font-weight:bold; color:#0056b3; margin-bottom:3px;'>🟢 {u_info['name']} 님 로그인 중</div><div style='font-size:14px; color:#333; margin-bottom:2px;'>📘 과목: {u_info.get('subject', '전체')}</div><div style='font-size:14px; color:#333; margin-bottom:2px;'>🏫 소속: {u_info.get('class_group', '')}</div><div style='font-size:14px; color:#333;'>🛡️ 권한: {u_info['role']}</div></div>"
        
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
        reg_id = st.sidebar.text_input("학번 입력")
        reg_name = st.sidebar.text_input("이름 입력")
        reg_pw = st.sidebar.text_input("비밀번호", type="password")
        
        if st.sidebar.button("가입 신청", type="primary", use_container_width=True):
            if reg_subject and reg_class and reg_id and reg_name and reg_pw:
                user_key = f"{reg_subject.strip()}_{reg_class.strip()}_{reg_id.strip()}"
                fresh_users = load_json(USERS_FILE, {}) 
                if user_key in fresh_users:
                    st.sidebar.error("❌ 해당 학번이 이미 가입되어 있습니다.")
                else:
                    fresh_users[user_key] = {
                        "id": reg_id.strip(), "password": reg_pw.strip(), "name": reg_name.strip(), 
                        "role": "학생", "subject": reg_subject.strip(), "class_group": reg_class.strip(), "approved": False
                    }
                    save_json(USERS_FILE, fresh_users)
                    st.sidebar.success("🎉 가입 완료! 선생님의 승인을 기다려주세요.")
            else: st.sidebar.warning("⚠️ 모든 빈칸을 빠짐없이 입력해주세요.")
                
    elif auth_choice == "로그인":
        login_type = st.sidebar.radio("계정 유형", ["학생", "교사(관리자)"], horizontal=True)
        if login_type == "학생":
            login_subject = st.sidebar.selectbox("과목", SUBJECTS)
            login_class = st.sidebar.selectbox("반", CLASSES_MAP[login_subject])
            input_id = st.sidebar.text_input("학번")
            input_pw = st.sidebar.text_input("비밀번호", type="password")
            
            if st.sidebar.button("로그인", type="primary", use_container_width=True):
                user_key = f"{login_subject.strip()}_{login_class.strip()}_{input_id.strip()}"
                if user_key in users and users[user_key].get("password") == input_pw.strip():
                    if users[user_key].get("approved", True):
                        st.session_state.logged_in = True
                        st.session_state.user_info = users[user_key]
                        st.session_state.user_info["user_key"] = user_key
                        st.query_params["session_token"] = encode_token(user_key)
                        st.rerun()
                    else: st.sidebar.warning("⏳ 선생님의 가입 승인을 대기 중입니다.")
                else: st.sidebar.error("❌ 과목, 반, 학번 또는 비밀번호가 틀렸습니다.")
        else:
            input_id = st.sidebar.text_input("관리자 ID")
            input_pw = st.sidebar.text_input("비밀번호", type="password")
            if st.sidebar.button("로그인", type="primary", use_container_width=True):
                if input_id in ADMIN_ACCOUNTS and input_pw == ADMIN_ACCOUNTS[input_id]["pw"]:
                    admin_info = ADMIN_ACCOUNTS[input_id]
                    st.session_state.logged_in = True
                    st.session_state.user_info = {
                        "user_key": input_id, "id": input_id, "name": admin_info["name"], 
                        "role": "관리자", "subject": "전체", "class_group": "관리자"
                    }
                    st.query_params["session_token"] = encode_token(input_id)
                    st.rerun()
                else: st.sidebar.error("❌ 관리자 정보가 틀렸습니다.")

st.sidebar.markdown("---")
st.sidebar.markdown("<div style='text-align: center; color: #222; font-size: 18px; font-weight: 900;'>Made by<br><span style='font-size: 24px; color: #000; font-weight: 900;'>신선여자고등학교 김명남</span></div>", unsafe_allow_html=True)

# --- [5] 메인 화면 분기 로직 ---
if not st.session_state.logged_in:
    st.title("🏫 수업 및 활동 어시스트 프로그램")
    st.info("왼쪽 사이드바를 이용해 로그인해주세요.")

else:
    current_role = st.session_state.user_info["role"]
    current_user_key = st.session_state.user_info["user_key"]
    u_info = st.session_state.user_info
    
    app_config = load_json(CONFIG_FILE, {})
    learning_data = load_json(DATA_FILE, {})

    if st.session_state.current_page in ACTIVITIES:
        act_name = st.session_state.current_page
        st.title(f"📄 {act_name}")
        st.markdown("---")
        
        if act_name == ACTIVITIES[0]: render_activity1(current_user_key, u_info['name'], current_role)
        elif act_name == ACTIVITIES[1]: render_activity2(current_user_key, u_info['name'], current_role)
        elif act_name == ACTIVITIES[2]: render_activity3(current_user_key, u_info['name'], current_role)
        
        st.markdown("<br><br>", unsafe_allow_html=True)
        if st.button("⬅️ 메인 화면으로 돌아가기", use_container_width=True):
            change_page("main")

    else:
        st.session_state.current_page = "main"
        
        if current_role == "학생":
            st.title("🏫 수업 및 활동 어시스트 프로그램")
            render_class_overview(current_role, u_info)

            student_answers = learning_data.get(current_user_key, {})
            if student_answers:
                st.markdown("---")
                st.subheader("📚 내 포트폴리오 전체 일괄 다운로드")
                html_content = generate_portfolio_html(student_answers, u_info['name'], u_info['class_group'], u_info['subject'])
                st.download_button(label=f"📥 {u_info['name']} 학생 전체 포트폴리오 다운로드 (웹문서)", data=html_content.encode('utf-8-sig'), file_name=f"{u_info['name']}_전체_포트폴리오.html", mime="text/html", type="primary")
                st.caption("💡 다운로드한 파일을 인터넷 창으로 연 뒤 **[우클릭 ➔ 인쇄 ➔ PDF로 저장]** 하시면 제출용 파일이 완성됩니다.")

        elif current_role == "관리자":
            st.title("🛠️ 관리자(교사) 대시보드")
            menu_tabs = st.tabs(["📌 수업 공지 및 기한 설정", "👥 회원 관리", "📥 학생 자료 조회", "💾 데이터 백업 및 복구(중요)"])
            
            with menu_tabs[0]:
                if st.session_state.get("admin_save_success", False):
                    st.balloons()
                    st.markdown("""
                    <div style='text-align: center; padding: 25px; background-color: #e8f5e9; color: #2e7d32; border-radius: 15px; border: 3px solid #4CAF50; margin: 20px 0; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
                        <h2 style='margin:0 0 10px 0; color: #2e7d32;'>🎉 화면 저장이 완료되었습니다!</h2>
                        <p style='margin:0; font-size: 18px; font-weight: bold;'>변경하신 내용이 데이터베이스에 안전하게 저장되어 즉시 반영됩니다.</p>
                    </div>
                    """, unsafe_allow_html=True)
                    st.session_state.admin_save_success = False

                render_class_overview(current_role, u_info)
                st.markdown("---")

                st.subheader("📢 메인 화면 내용 추가/수정/삭제 (자유 양식)")
                st.info("💡 아래 표에 텍스트를 입력하면 학생들의 메인 화면 상단에 즉시 공지사항으로 표시됩니다. 표의 빈칸을 더블클릭하여 내용을 작성하고 행을 추가/삭제할 수 있습니다.")
                fresh_config = load_json(CONFIG_FILE, {})

                current_notices = fresh_config.get("notices", [])
                df_notices = pd.DataFrame(current_notices) if current_notices else pd.DataFrame([{"제목": "", "내용": ""}])

                edited_notices = st.data_editor(df_notices, num_rows="dynamic", use_container_width=True, hide_index=True)

                if st.button("메인 화면 공지사항 저장 및 적용", type="primary"):
                    valid_notices = []
                    for row in edited_notices.to_dict('records'):
                        if str(row.get("제목", "")).strip() or str(row.get("내용", "")).strip():
                            valid_notices.append(row)
                    fresh_config["notices"] = valid_notices
                    save_json(CONFIG_FILE, fresh_config)
                    st.session_state.admin_save_success = True
                    st.rerun()

                st.markdown("---")
                st.subheader("⏰ 수행평가 제출 기간(타이머) 설정")
                st.info("💡 설정한 기간 외에는 학생들이 활동지에 내용을 입력하거나 저장(제출)할 수 없도록 완벽하게 차단됩니다.")
                deadlines = fresh_config.get("deadlines", {})
                
                with st.form("deadline_form"):
                    new_deadlines = {}
                    for act in ACTIVITIES:
                        st.markdown(f"**{act}**")
                        cols = st.columns(4)
                        c_start = deadlines.get(act, {}).get("start", "2026-01-01 00:00")
                        c_end = deadlines.get(act, {}).get("end", "2030-12-31 23:59")
                        try:
                            s_dt = datetime.datetime.strptime(c_start, "%Y-%m-%d %H:%M")
                            e_dt = datetime.datetime.strptime(c_end, "%Y-%m-%d %H:%M")
                        except:
                            s_dt = datetime.datetime.now()
                            e_dt = datetime.datetime.now() + datetime.timedelta(days=7)
                        
                        s_date = cols[0].date_input("시작일", value=s_dt.date(), key=f"sd_{act}")
                        s_time = cols[1].time_input("시작시간", value=s_dt.time(), key=f"st_{act}")
                        e_date = cols[2].date_input("종료일", value=e_dt.date(), key=f"ed_{act}")
                        e_time = cols[3].time_input("종료시간", value=e_dt.time(), key=f"et_{act}")
                        
                        new_deadlines[act] = {"start": f"{s_date} {s_time.strftime('%H:%M')}", "end": f"{e_date} {e_time.strftime('%H:%M')}"}
                        st.markdown("<br>", unsafe_allow_html=True)
                    
                    if st.form_submit_button("제출 기간 저장 및 적용", type="primary"):
                        fresh_config["deadlines"] = new_deadlines
                        save_json(CONFIG_FILE, fresh_config)
                        st.session_state.admin_save_success = True
                        st.rerun()

                st.markdown("---")
                st.subheader("👨‍🏫 교사용 수업 자료 업로드 (공지사항용)")
                with st.form("upload_mat"):
                    mat_subj = st.selectbox("대상 과목", ["전체 공지"] + SUBJECTS)
                    mat_title = st.text_input("자료 제목")
                    mat_link = st.text_input("외부 링크 URL (있는 경우)")
                    if st.form_submit_button("등록", type="primary"):
                        if mat_title and mat_link:
                            new_mat = {"id": f"mat_{datetime.datetime.now().strftime('%d%H%M%S')}", "title": mat_title, "type": "link", "content": mat_link, "subject": mat_subj}
                            if "materials" not in fresh_config: fresh_config["materials"] = []
                            fresh_config["materials"].append(new_mat)
                            save_json(CONFIG_FILE, fresh_config)
                            st.session_state.admin_save_success = True
                            st.rerun()

            with menu_tabs[1]:
                all_users = load_json(USERS_FILE, {})
                pending_users = {k: v for k, v in all_users.items() if not v.get("approved", True) and v.get("role")=="학생"}
                approved_users = {k: v for k, v in all_users.items() if v.get("approved", True) and v.get("role")=="학생"}
                
                st.subheader("⏳ 가입 승인 대기 목록")
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
                st.subheader("✅ 기존 승인된 학생 목록 (삭제 관리)")
                col1, col2 = st.columns(2)
                with col1:
                    filter_subj = st.selectbox("조회할 과목 선택", ["전체"] + SUBJECTS, key="manage_subj")
                with col2:
                    target_classes = ["전체"] + CLASSES_MAP.get(filter_subj, []) if filter_subj != "전체" else ["전체"] + [c for cl in CLASSES_MAP.values() for c in cl]
                    filter_class = st.selectbox("조회할 반 선택", target_classes, key="manage_class")
                
                filtered_approved = {k: v for k, v in approved_users.items() if (filter_subj == "전체" or v.get("subject", "").strip() == filter_subj.strip()) and (filter_class == "전체" or v.get("class_group", "").strip() == filter_class.strip())}
                df_users = pd.DataFrame([{"과목": v.get("subject", "-"), "반": v.get("class_group", "-"), "학번": v.get("id", "-"), "이름": v.get("name", "-"), "비밀번호": v.get("password", "-")} for k, v in filtered_approved.items()])
                st.dataframe(df_users, use_container_width=True)
                
                if filtered_approved:
                    del_target = st.selectbox("삭제할 학생 선택", ["선택"] + list(filtered_approved.keys()), format_func=lambda x: x if x=="선택" else f"[{filtered_approved[x].get('class_group')}] {filtered_approved[x].get('name')} ({filtered_approved[x].get('id')})")
                    if del_target != "선택" and st.button("⚠️ 해당 학생 영구 삭제", type="primary"):
                        fresh_users = load_json(USERS_FILE, {})
                        if del_target in fresh_users: del fresh_users[del_target]
                        save_json(USERS_FILE, fresh_users)
                        st.success("삭제 완료"); st.rerun()

            with menu_tabs[2]:
                col_t, col_b = st.columns([8, 2])
                with col_t: st.subheader("📥 학생 학습 활동 및 제출 자료 조회")
                with col_b: 
                    if st.button("🔄 최신 데이터 새로고침", type="primary"): 
                        st.rerun()
                
                all_users = load_json(USERS_FILE, {})
                learning_data = load_json(DATA_FILE, {})
                
                c1, c2 = st.columns(2)
                view_subj = c1.selectbox("조회할 과목", SUBJECTS, key="view_subj_select")
                available_classes = CLASSES_MAP.get(view_subj, [])
                view_class = c2.selectbox("조회할 반", ["전체 보기"] + available_classes, key="view_class_select")
                
                student_list = []
                for uid, info in all_users.items():
                    if info.get("role") == "학생":
                        s_subj = info.get("subject", "").strip()
                        s_class = info.get("class_group", "").strip()
                        
                        target_subj = view_subj.strip()
                        target_class = view_class.strip()
                        
                        if s_subj == target_subj:
                            if target_class == "전체 보기" or s_class == target_class:
                                student_list.append(uid)
                
                if not student_list:
                    st.info("해당 조건에 등록된 학생이 없습니다. 가입 승인 대기 목록을 확인하거나, 상단 [🔄 최신 데이터 새로고침] 버튼을 눌러보세요.")
                else:
                    st.markdown("### 🗂️ 필터링된 학생 전체 포트폴리오 일괄 다운로드")
                    st.info("현재 선택된 과목 및 반의 모든 학생 포트폴리오를 하나의 ZIP 파일로 묶어서 다운로드합니다.")
                    
                    zip_buffer = io.BytesIO()
                    has_data = False
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                        for s_uid in student_list:
                            s_ans = learning_data.get(s_uid, {})
                            if s_ans:
                                u_n = all_users[s_uid].get('name', '학생')
                                u_c = all_users[s_uid].get('class_group', '')
                                h_content = generate_portfolio_html(s_ans, u_n, u_c, view_subj)
                                file_n = f"{u_c}_{u_n}_포트폴리오.html"
                                zip_file.writestr(file_n, h_content.encode('utf-8-sig'))
                                has_data = True
                    
                    if has_data:
                        st.download_button(
                            label=f"📦 전체 학생({len(student_list)}명) 포트폴리오 ZIP 일괄 다운로드",
                            data=zip_buffer.getvalue(),
                            file_name=f"{view_subj}_{view_class}_전체포트폴리오.zip",
                            mime="application/zip",
                            type="primary"
                        )
                    else:
                        st.warning("제출된 데이터가 없어 일괄 다운로드를 생성할 수 없습니다.")
                        
                    st.markdown("---")

                    view_mode = st.radio("조회 모드", ["👤 특정 학생 집중 분석 (화면 확인 및 포트폴리오 다운로드)", "📅 항목별 전체 현황 (엑셀 CSV)"], horizontal=True)
                    st.markdown("---")
                    
                    if view_mode == "👤 특정 학생 집중 분석 (화면 확인 및 포트폴리오 다운로드)":
                        def format_student_dropdown(x):
                            appr_str = "" if all_users[x].get("approved", True) else " (미승인)"
                            return f"[{all_users[x].get('class_group')}] {all_users[x].get('name')} ({all_users[x].get('id')}){appr_str}"
                            
                        selected_student = st.selectbox("학생 선택", student_list, format_func=format_student_dropdown)
                        if selected_student:
                            student_answers = learning_data.get(selected_student, {})
                            u_name = all_users[selected_student].get('name', '학생')
                            u_class_selected = all_users[selected_student].get('class_group', '')
                            
                            st.markdown(f"### 📋 {u_name} 학생 제출 내용 바로 확인하기")
                            
                            has_answer = False
                            for act in ACTIVITIES:
                                ans = student_answers.get(act, {})
                                if ans:
                                    has_answer = True
                                    st.markdown(f"#### {act}")
                                    if act == ACTIVITIES[0]:
                                        st.markdown("##### [1. 자신이 선택한 영상에 대한 첫번째 질문]")
                                        st.write(f"- **영상의 제목:** {ans.get('a1_1','')} | **국가 혹은 지역:** {ans.get('a1_2','')}")
                                        st.info(f"**선택 이유:**\n{ans.get('a1_3','')}")
                                        
                                        st.markdown("##### [2. 자신이 선택한 영상에 대한 두 번째 질문]")
                                        st.info(f"**첫 느낌:**\n{ans.get('a2_1','')}")
                                        st.write(f"- **인상적이었던 장소:** {ans.get('a2_2_1','')}")
                                        st.write(f"- **그 이유:** {ans.get('a2_2_2','')}")
                                        st.write(f"- **누구에게 추천:** {ans.get('a2_3_1','')}")
                                        st.write(f"- **추천 이유:** {ans.get('a2_3_2','')}")
                                        st.info(f"**나만의 감상평:**\n{ans.get('a2_4','')}")
                                        
                                        st.markdown("##### [5. 만일 내가 영상 속 지역을 배경으로 영상을 찍는다면?]")
                                        st.write(f"- **1) 영상의 제목:** {ans.get('a3_1','')}")
                                        st.write(f"- **2) 주요 컨셉 혹은 느낌:** {ans.get('a3_2','')}")
                                        st.write(f"- **3) 누구와 함께 가고 싶은가?:** {ans.get('a3_3','')}")
                                        st.info(f"**4) 그 이유는?:**\n{ans.get('a3_4','')}")
                                        st.write(f"- **5) 가장 해 보고 싶은 것:** {ans.get('a3_5','')}")
                                        st.info(f"**6) 그 이유는?:**\n{ans.get('a3_6','')}")
                                        st.write(f"- **7) 꼭 넣고 싶은 장소/공간:** {ans.get('a3_7','')}")
                                        st.info(f"**8) 그 이유는?:**\n{ans.get('a3_8','')}")
                                        st.info(f"**9) 썸네일 영상 기획:**\n{ans.get('a3_9','')}")
                                        st.write(f"- **10) 어울리는 BGM:** {ans.get('a3_10','')}")
                                        st.info(f"**11) 그 이유는?:**\n{ans.get('a3_11','')}")

                                    elif act == ACTIVITIES[1]:
                                        st.write(f"- **편안한 장소:** {ans.get('q1_1','')} | **이유:** {ans.get('q1_2','')}")
                                        st.write(f"- **성격:** {ans.get('q2_1','')} | **영향 장소:** {ans.get('q2_2','')}")
                                        st.write(f"- **장점:** {ans.get('q3_1','')} | **영향 장소:** {ans.get('q3_2','')}")
                                        st.write(f"- **성장 장소:** {ans.get('q4_1','')} | **이유:** {ans.get('q4_2','')}")
                                        st.write(f"- **목표:** {ans.get('q5_1','')} | **영향 장소:** {ans.get('q5_2','')}")
                                        st.write(f"- **소개할 장소:** {ans.get('q6_1','')} | **이유:** {ans.get('q6_2','')}")
                                        st.write(f"- **비밀 장소:** {ans.get('q7_1','')} | **이유:** {ans.get('q7_2','')}")
                                        st.write(f"- **과거로 간다면:** {ans.get('q8_1','')} | **이유:** {ans.get('q8_2','')}")

                                    elif act == ACTIVITIES[2]:
                                        st.write("**[세계 인식 수준 확인]**")
                                        st.dataframe(pd.DataFrame(ans.get("s1_df", [])), use_container_width=True)
                                        st.dataframe(pd.DataFrame(ans.get("direct_df", [])), use_container_width=True)
                                        st.write(f"영화: {ans.get('ind1','')} / 음악: {ans.get('ind2','')} / 음식: {ans.get('ind3','')}")
                                        st.write("**[가고 싶은 곳 / 가기 싫은 곳]**")
                                        st.dataframe(pd.DataFrame(ans.get("top5_want", [])), use_container_width=True)
                                        st.dataframe(pd.DataFrame(ans.get("top5_notwant", [])), use_container_width=True)
                                        st.write("**[편견과 고정관념]**")
                                        st.dataframe(pd.DataFrame(ans.get("label_df", [])), use_container_width=True)
                                        st.dataframe(pd.DataFrame(ans.get("prej_df", [])), use_container_width=True)
                                        st.write(f"뉴스: {ans.get('media1_1','')} ({ans.get('media1_2','')})")
                                        st.write(f"영화: {ans.get('media2_1','')} ({ans.get('media2_2','')})")
                                        st.write(f"학교: {ans.get('media3_1','')} ({ans.get('media3_2','')})")
                                        st.dataframe(pd.DataFrame(ans.get("fake_df", [])), use_container_width=True)
                                        st.dataframe(pd.DataFrame(ans.get("discrim_df", [])), use_container_width=True)
                                        st.write("**[포용적 세계관 노력]**")
                                        st.dataframe(pd.DataFrame(ans.get("change_df", [])), use_container_width=True)
                                        st.dataframe(pd.DataFrame(ans.get("ignore_df", [])), use_container_width=True)
                                        st.dataframe(pd.DataFrame(ans.get("western_df", [])), use_container_width=True)
                                        st.write("**[목표로 하는 세계관]**")
                                        st.info(f"**어떤 사람이 되고 싶은가?**\n{ans.get('goal_1','')}")
                                        st.info(f"**어떤 세계관을 갖고 싶은가?**\n{ans.get('goal_2','')}")
                            
                            if not has_answer:
                                st.warning("아직 제출한 활동지 내역이 없는 학생입니다.")
                            
                            st.markdown("---")
                            html_content = generate_portfolio_html(student_answers, u_name, u_class_selected, view_subj)
                            st.download_button(label=f"📄 {u_name} 학생 개별 포트폴리오 다운로드 (웹문서)", data=html_content.encode('utf-8-sig'), file_name=f"{u_name}_{view_subj}_포트폴리오.html", mime="text/html", type="primary")

                    elif view_mode == "📅 항목별 전체 현황 (엑셀 CSV)":
                        selected_view = st.selectbox("다운로드할 수행평가 선택", ACTIVITIES)
                        
                        csv_data = []
                        for s_uid in student_list:
                            ans = learning_data.get(s_uid, {}).get(selected_view, {})
                            u_info = all_users[s_uid]
                            u_id = u_info.get('id', '')
                            u_name = u_info.get('name', '')
                            u_class = u_info.get('class_group', '')
                            
                            if selected_view == ACTIVITIES[0]: 
                                csv_data.append({"반": u_class, "학번": u_id, "이름": u_name, "1.영상제목": ans.get("a1_1", ""), "2.국가/지역": ans.get("a1_2", ""), "3.선택이유": ans.get("a1_3", ""), "4.첫느낌": ans.get("a2_1", ""), "5.인상적인장소": ans.get("a2_2_1", ""), "6.장소이유": ans.get("a2_2_2", ""), "7.추천대상": ans.get("a2_3_1", ""), "8.추천이유": ans.get("a2_3_2", ""), "9.감상평": ans.get("a2_4", ""), "10.기획제목": ans.get("a3_1", ""), "11.컨셉": ans.get("a3_2", ""), "12.동행": ans.get("a3_3", ""), "13.동행이유": ans.get("a3_4", ""), "14.해볼것": ans.get("a3_5", ""), "15.해볼이유": ans.get("a3_6", ""), "16.넣을장소": ans.get("a3_7", ""), "17.넣을이유": ans.get("a3_8", ""), "18.썸네일": ans.get("a3_9", ""), "19.BGM": ans.get("a3_10", ""), "20.BGM이유": ans.get("a3_11", "")})
                            elif selected_view == ACTIVITIES[1]:
                                csv_data.append({"반": u_class, "학번": u_id, "이름": u_name, "1-1.편안한장소": ans.get("q1_1", ""), "1-2.이유": ans.get("q1_2", ""), "2-1.나의성격": ans.get("q2_1", ""), "2-2.성격영향장소": ans.get("q2_2", ""), "2-3.이유": ans.get("q2_3", ""), "3-1.나의장점": ans.get("q3_1", ""), "3-2.장점영향장소": ans.get("q3_2", ""), "3-3.이유": ans.get("q3_3", ""), "4-1.성장영향장소": ans.get("q4_1", ""), "4-2.이유": ans.get("q4_2", ""), "5-1.나의목표": ans.get("q5_1", ""), "5-2.목표영향장소": ans.get("q5_2", ""), "6-1.소개할장소": ans.get("q6_1", ""), "6-2.이유": ans.get("q6_2", ""), "7-1.비밀장소": ans.get("q7_1", ""), "7-2.이유": ans.get("q7_2", ""), "8-1.과거로간다면": ans.get("q8_1", ""), "8-2.이유": ans.get("q8_2", "")})
                            elif selected_view == ACTIVITIES[2]:
                                csv_data.append({
                                    "반": u_class, "학번": u_id, "이름": u_name,
                                    "1.대륙별인식": format_df_to_str(ans.get("s1_df", []), ["대륙", "관심도", "지식수준"]),
                                    "2.직접경험": format_df_to_str(ans.get("direct_df", []), ["여행해 본 국가", "해당 국가에 대한 구체적인 기억 혹은 인상"]),
                                    "3.간접경험(영화)": ans.get("ind1", ""), "4.간접경험(음악)": ans.get("ind2", ""), "5.간접경험(음식)": ans.get("ind3", ""),
                                    "6.가고싶은Top5": format_df_to_str(ans.get("top5_want", []), ["국가 혹은 지역", "이유"]),
                                    "7.가고싫은Top5": format_df_to_str(ans.get("top5_notwant", []), ["국가 혹은 지역", "이유"]),
                                    "8.단어라벨링": format_df_to_str(ans.get("label_df", []), ["가 보고 싶은 국가", "한 단어 라벨", "가고 싶지 않은 국가", "한 단어 라벨(부정)"]),
                                    "9.강한편견국가": format_df_to_str(ans.get("prej_df", []), ["국가명", "편견 내용", "편견 형성 과정 혹은 이유"]),
                                    "10.미디어영향": f"뉴스: {ans.get('media1_1','')} ({ans.get('media1_2','')}) / 영화: {ans.get('media2_1','')} ({ans.get('media2_2','')}) / 학교: {ans.get('media3_1','')} ({ans.get('media3_2','')})",
                                    "11.부정확한정보": format_df_to_str(ans.get("fake_df", []), ["국가명", "잘못 알고 있었던 내용", "실제 사실"]),
                                    "12.차별의식": format_df_to_str(ans.get("discrim_df", []), ["어떤 국가에 대해?", "어떤 측면에서", "그 이유"]),
                                    "13.편견바꾸기": format_df_to_str(ans.get("change_df", []), ["어떤 국가에 대해?", "현재의 편견", "올바른 정보를 찾기 위한 계획"]),
                                    "14.무관심국가": format_df_to_str(ans.get("ignore_df", []), ["선택 대륙/국가", "무관심 이유", "관심 확장을 위한 정보 수집 방법"]),
                                    "15.서구중심시각": format_df_to_str(ans.get("western_df", []), ["현재 가지고 있는 서구 중심적 시각", "개선 방법"]),
                                    "16.목표(어떤사람)": ans.get("goal_1", ""), "17.목표(세계관)": ans.get("goal_2", "")
                                })
                        
                        if csv_data:
                            df_csv = pd.DataFrame(csv_data)
                            st.dataframe(df_csv, use_container_width=True)
                            st.download_button(f"📊 엑셀 다운로드", data=df_csv.to_csv(index=False).encode('utf-8-sig'), file_name=f"{view_subj}_{view_class}_{selected_view[:6]}.csv", mime='text/csv', type="primary")
                        else:
                            st.info("해당 수행평가에 제출된 데이터가 없습니다.")

            with menu_tabs[3]:
                st.subheader("💾 데이터베이스 백업 및 복구 (매우 중요)")
                st.error("⚠️ 클라우드 서버 특성상 재부팅 시 저장 데이터가 초기화될 수 있습니다. 퇴근 전이나 수업 후 꼭 백업 파일(.json)을 내려받아 두세요!")
                
                all_users_str = load_json(USERS_FILE, {})
                all_data_str = load_json(DATA_FILE, {})
                backup_dict = {"users": all_users_str, "data": all_data_str}
                backup_json = json.dumps(backup_dict, ensure_ascii=False, indent=2)
                
                st.download_button("⬇️ 현재까지의 전체 데이터 백업 파일 다운로드 (.json)", data=backup_json.encode('utf-8-sig'), file_name=f"backup_DB_{datetime.datetime.now().strftime('%Y%m%d_%H%M')}.json", type="primary", use_container_width=True)
                
                st.markdown("---")
                st.subheader("♻️ 데이터 복구하기")
                uploaded_file = st.file_uploader("다운로드 해둔 백업 파일(.json)을 업로드하세요", type="json")
                if st.button("위 파일로 데이터 복구 실행", type="primary"):
                    if uploaded_file:
                        try:
                            restored = json.load(uploaded_file)
                            save_json(USERS_FILE, restored.get("users", {}))
                            save_json(DATA_FILE, restored.get("data", {}))
                            st.success("🎉 데이터 복구가 완벽히 완료되었습니다!"); st.rerun()
                        except:
                            st.error("❌ 올바른 백업 파일이 아닙니다.")
