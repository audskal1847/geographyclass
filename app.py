import streamlit as st
import streamlit.components.v1 as components
import json, os, base64, datetime, threading, io, zipfile
import pandas as pd
from PIL import Image

def process_sketch_image(uploaded_file, max_width=800):
    """스마트폰 고화질 사진을 경량화하여 JSON에 안전하게 저장 가능한 Base64로 변환"""
    if uploaded_file is None:
        return ""
    try:
        img = Image.open(uploaded_file)
        if img.mode in ("RGBA", "P"):
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

def process_uploaded_video(uploaded_file, max_mb=10):
    """직접 제작한 시뮬레이션 영상(MP4/GIF)을 Base64로 안전하게 변환 (용량 초과 방지)"""
    if uploaded_file is None:
        return ""
    if uploaded_file.size > max_mb * 1024 * 1024:
        st.error(f"⚠️ 영상 파일 용량이 {max_mb}MB를 초과했습니다. 더 짧거나 작은 파일로 올려주세요.")
        return ""
    bytes_data = uploaded_file.read()
    mime = uploaded_file.type or "video/mp4"
    b64 = base64.b64encode(bytes_data).decode("utf-8")
    return f"data:{mime};base64,{b64}"        

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
    schedule_strs, is_time_match = [], False
    for slot in slots:
        if slot['day'] != "선택안함":
            p_str = f" {slot.get('period', '')}" if slot.get('period', '') and slot.get('period') != "선택안함" else ""
            schedule_strs.append(f"{slot['day']}요일{p_str} {slot['start']} - {slot['end']}")
            if slot['day'] == current_day:
                try:
                    if datetime.datetime.strptime(slot["start"], "%H:%M").time() <= current_time <= datetime.datetime.strptime(slot["end"], "%H:%M").time(): is_time_match = True
                except: continue
    sched_display = ", ".join(schedule_strs) if schedule_strs else "설정된 수업 시간 없음"
    if is_time_match: return True, "✅ 현재 수업 시간입니다. 정상적으로 작성하고 저장(제출)할 수 있습니다."
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

def load_json(file_path, default_value):
    with db_lock:
        if not os.path.exists(file_path):
            with open(file_path, "w", encoding="utf-8") as f: json.dump(default_value, f, ensure_ascii=False, indent=4)
            return default_value
        for _ in range(5):
            try:
                with open(file_path, "r", encoding="utf-8") as f: return json.load(f)
            except: import time; time.sleep(0.1)
        return default_value

def save_json(file_path, data):
    with db_lock:
        with open(file_path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=4)

def create_auto_backup(reason="자동 스냅샷"):
    with db_lock:
        try:
            now_str = get_kst_now().strftime("%Y%m%d_%H%M%S")
            backup_bundle = {"timestamp": get_kst_now().strftime("%Y-%m-%d %H:%M:%S"), "reason": reason, "users": load_json(USERS_FILE, {}), "learning_data": load_json(DATA_FILE, {}), "config": load_json(CONFIG_FILE, {})}
            with open(os.path.join(BACKUP_DIR, f"backup_{now_str}.json"), "w", encoding="utf-8") as f: json.dump(backup_bundle, f, ensure_ascii=False, indent=2)
            all_bks = sorted([os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR) if f.startswith("backup_") and f.endswith(".json")])
            if len(all_bks) > 30:
                for old_bk in all_bks[:-30]:
                    try: os.remove(old_bk)
                    except: pass
        except: pass

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

def get_user_activity_data(user_key, u_id, u_subj, u_class, act_name, learning_data):
    if act_name in [ACT_2_1, ACT_2_2]:
        u_id_str = str(u_id).strip()
        if not u_id_str: return user_key, learning_data.get(user_key, {}).get(act_name, {})
        own_data = learning_data.get(user_key, {}).get(act_name, {})
        if str(own_data.get("m1_id", "")).strip() == u_id_str: return user_key, own_data
        for k, acts in learning_data.items():
            if k.startswith(f"{u_subj}_{u_class}_"):
                members = [str(acts.get(act_name, {}).get(f"m{i}_id", "")).strip() for i in range(1, 5)]
                if u_id_str in members: return k, acts.get(act_name, {})
    return user_key, learning_data.get(user_key, {}).get(act_name, {})

def get_act_csv_rows(selected_view, ans, config=None):
    if ans is None:
        ans = {}
    csv_data = []

    # ----------------------------------------------------
    # 3학년 여행지리 수행평가 1
    # ----------------------------------------------------
    if selected_view == ACT_3_1:
        csv_data.extend([
            ["[1. 자신이 선택한 영상에 대한 첫번째 질문]", ""],
            ["1. 영상의 제목", ans.get("a1_1", "")],
            ["2. 영상(다큐멘터리/영화 등)의 줄거리 요약", ans.get("a1_2", "")],
            ["3. 영상에서 다루는 주요 지리적/문화적 배경", ans.get("a1_3", "")]
        ])

    # ----------------------------------------------------
    # 3학년 여행지리 수행평가 2
    # ----------------------------------------------------
    elif selected_view == ACT_3_2:
        csv_data.extend([
            ["1-1) 나에게 편안함을 주는 장소(공간)이/가 있는가?", ans.get("q1_1", "")],
            ["1-2) 그 장소(공간)이/가 편안함을 주는 이유는 무엇인가?", ans.get("q1_2", "")],
            ["2-1) 나에게 불편함을 주는 장소(공간)이/가 있는가?", ans.get("q2_1", "")],
            ["2-2) 그 장소(공간)이/가 불편함을 주는 이유는 무엇인가?", ans.get("q2_2", "")]
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
        
        csv_data.append(["[Step 2. 도시 개조 포인트를 활용한 트레이드오프 설계]", ""])
        if ans.get("step2_custom_df"):
            for row in ans.get("step2_custom_df", []):
                if row.get("세부 개조 항목"):
                    csv_data.append([f"[추가개조] {row.get('코드','')}", f"{row.get('세부 개조 항목','')} ({row.get('비용(10~20pt)','')})"])
        
        csv_data.append(["[Step 4. 3분 공청회 발표를 위한 준비]", ""])
        csv_data.extend([
            ["1. 핵심 정책 슬로건", ans.get("step4_1", "")],
            ["2. 실제 답사 및 데이터로 확인한 선택한 지역의 가장 심각한 공간 문제는 무엇이라고 생각하는가?", ans.get("step4_2", "")],
            ["3. 한정된 100pt를 활용해 무엇을 버리고 무엇을 채웠는가? 그 이유는 무엇인가?", ans.get("step4_3", "")],
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
        sim_video = ans.get("step4_video", "")
        if sim_video:
            if "data:video" in sim_video:
                v_tag = f"<video src='{sim_video}' controls style='max-width:450px; border-radius:8px; border:1px solid #94a3b8;'></video>"
            else:
                v_tag = f"<img src='{sim_video}' style='max-width:450px; border-radius:8px; border:1px solid #94a3b8; box-shadow:0 4px 6px rgba(0,0,0,0.1);'>"
            csv_data.append(["[최종] 미디어 파사드 시뮬레이션 영상", v_tag])
        else:
            csv_data.append(["[최종] 시뮬레이션 영상", "(영상 미등록)"])
            
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
        html += f"<h4>[1. 자신이 선택한 영상에 대한 첫번째 질문]</h4>"
        html += f"<p><b>1. 영상의 제목:</b> {ans.get('a1_1', '')}</p>"
        html += f"<p><b>2. 영상(다큐멘터리/영화 등)의 줄거리 요약:</b><br>{ans.get('a1_2', '')}</p>"
        html += f"<p><b>3. 영상에서 다루는 주요 지리적/문화적 배경:</b><br>{ans.get('a1_3', '')}</p>"

    # ----------------------------------------------------
    # 3학년 여행지리 수행평가 2
    # ----------------------------------------------------
    elif act_name == ACT_3_2:
        html += f"<p><b>1-1) 나에게 편안함을 주는 장소(공간)이/가 있는가?:</b> {ans.get('q1_1', '')}</p>"
        html += f"<p><b>1-2) 그 장소(공간)이/가 편안함을 주는 이유는 무엇인가?:</b><br>{ans.get('q1_2', '')}</p>"
        html += f"<p><b>2-1) 나에게 불편함을 주는 장소(공간)이/가 있는가?:</b> {ans.get('q2_1', '')}</p>"
        html += f"<p><b>2-2) 그 장소(공간)이/가 불편함을 주는 이유는 무엇인가?:</b><br>{ans.get('q2_2', '')}</p>"

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
        html += "<h3>Step 1. 우리 동네 현황 진단</h3>"
        html += f"<p><b>1. 대상 지역:</b> {ans.get('step1_1','')}</p>"
        html += "<h4>2. 15분 생활권 반경 내 필수 서비스 체크리스트</h4>"
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

        html += "<h3>Step 2. 도시 개조 포인트를 활용한 트레이드오프 설계</h3>"
        html += """
        <table>
            <tr><th>카테고리</th><th>코드</th><th>세부 개조 항목</th><th>비용</th></tr>
            <tr><td rowspan="6" style="text-align:center; vertical-align:middle; font-weight:bold;">안전한 보행 환경</td><td style="text-align:center;">A-1</td><td>여고생 안심 하교길 스마트 로드</td><td style="text-align:center;">-15pt</td></tr>
            <tr><td style="text-align:center;">A-2</td><td>아파트 단지 간 담장 철거 및 공공 보행로 연결</td><td style="text-align:center;">-20pt</td></tr>
            <tr><td style="text-align:center;">A-3</td><td>차로 축소 및 쾌적한 보행을 위한 녹지 공간 조성</td><td style="text-align:center;">-20pt</td></tr>
            <tr><td style="text-align:center;">A-4</td><td>스마트 횡단보도 및 교통약자/학생 쉼터</td><td style="text-align:center;">-10pt</td></tr>
            <tr><td style="text-align:center;">A-5</td><td></td><td style="text-align:center;"></td></tr>
            <tr><td style="text-align:center;">A-6</td><td></td><td style="text-align:center;"></td></tr>
            <tr><td rowspan="5" style="text-align:center; vertical-align:middle; font-weight:bold;">녹지 및 생태공간 구축</td><td style="text-align:center;">B-1</td><td>아파트 상가/방치 공터 → 도심 소공원 조성</td><td style="text-align:center;">-15pt</td></tr>
            <tr><td style="text-align:center;">B-2</td><td>도심 바람길 숲 및 수변 산책로 조성</td><td style="text-align:center;">-15pt</td></tr>
            <tr><td style="text-align:center;">B-3</td><td>에코 펫파크(반려견 전용 공원 및 산책로)</td><td style="text-align:center;">-15pt</td></tr>
            <tr><td style="text-align:center;">B-4</td><td></td><td style="text-align:center;"></td></tr>
            <tr><td style="text-align:center;">B-5</td><td></td><td style="text-align:center;"></td></tr>
            <tr><td rowspan="5" style="text-align:center; vertical-align:middle; font-weight:bold;">문화와 교육을 위한 공간</td><td style="text-align:center;">C-1</td><td>24시간 공공 스터디 & 커뮤니티 카페</td><td style="text-align:center;">-15pt</td></tr>
            <tr><td style="text-align:center;">C-2</td><td>청소년 팝업 스튜디오 & 소공연장</td><td style="text-align:center;">-15pt</td></tr>
            <tr><td style="text-align:center;">C-3</td><td>친환경 스마트 팜</td><td style="text-align:center;">-10pt</td></tr>
            <tr><td style="text-align:center;">C-4</td><td></td><td style="text-align:center;"></td></tr>
            <tr><td style="text-align:center;">C-5</td><td></td><td style="text-align:center;"></td></tr>
            <tr><td rowspan="4" style="text-align:center; vertical-align:middle; font-weight:bold;">효율적인 교통과 모빌리티 구축</td><td style="text-align:center;">D-1</td><td>공유 자전거 및 킥보드 전용 도로</td><td style="text-align:center;">-15pt</td></tr>
            <tr><td style="text-align:center;">D-2</td><td>스마트 버스 쉘터(공기 청정, 냉난방 설비 구축)</td><td style="text-align:center;">-10pt</td></tr>
            <tr><td style="text-align:center;">D-3</td><td></td><td style="text-align:center;"></td></tr>
            <tr><td style="text-align:center;">D-4</td><td></td><td style="text-align:center;"></td></tr>
        </table>
        """
        if ans.get("step2_custom_df"):
            custom_rows = [r for r in ans.get("step2_custom_df", []) if r.get("세부 개조 항목")]
            if custom_rows:
                html += "<h4>▶ 모둠 신규 추가 개조 항목</h4><table><tr><th>코드</th><th>세부 개조 항목</th><th>비용</th></tr>"
                for r in custom_rows:
                    html += f"<tr><td style='text-align:center;'>{r.get('코드','')}</td><td>{r.get('세부 개조 항목','')}</td><td style='text-align:center;'>{r.get('비용(10~20pt)','')}</td></tr>"
                html += "</table>"

        html += "<h3>Step 4. 3분 공청회 발표를 위한 준비</h3>"
        html += f"<p><b>1. 핵심 정책 슬로건:</b></p><div class='content-box'>{ans.get('step4_1','')}</div>"
        html += "<div style='text-align: center; font-weight: bold; background-color: #eee; padding: 6px; margin: 15px 0;'>연설 내용 구조화 스크립트 작성</div>"
        html += f"<p><b>2. 실제 답사 및 데이터로 확인한 선택한 지역의 가장 심각한 공간 문제는 무엇이라고 생각하는가?:</b></p><div class='content-box'>{ans.get('step4_2','')}</div>"
        html += f"<p><b>3. 한정된 100pt를 활용해 무엇을 버리고 무엇을 채웠는가? 그 이유는 무엇인가?:</b></p><div class='content-box'>{ans.get('step4_3','')}</div>"
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
        sim_v = ans.get("step4_video", "")
        if sim_v:
            html += "<div style='margin-top:15px; padding:15px; background-color:#f8fafc; border-radius:8px; border:1px solid #e2e8f0; text-align:center;'>"
            html += "<p style='font-weight:bold; font-size:15px; margin-bottom:10px; color:#1e293b;'>🎬 미디어 파사드 모션 시뮬레이션</p>"
            if "data:video" in sim_v:
                html += f"<video src='{sim_v}' controls style='max-width:520px; border-radius:6px; box-shadow:0 2px 6px rgba(0,0,0,0.1);'></video>"
            else:
                html += f"<img src='{sim_v}' style='max-width:520px; border-radius:6px; box-shadow:0 2px 6px rgba(0,0,0,0.1);'>"
            html += "</div>"

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

def inject_custom_scripts():
    components.html("""<script>document.addEventListener("DOMContentLoaded", function() { const parentDoc = window.parent.document; function initAutoSave() { const elements = parentDoc.querySelectorAll('input[type="text"], textarea'); elements.forEach(el => { const ariaLabel = el.getAttribute('aria-label') || ''; const key = 'autosave_' + window.parent.location.pathname + '_' + ariaLabel; if (!el.dataset.autosaveAttached && ariaLabel !== '') { el.dataset.autosaveAttached = "true"; el.addEventListener('input', () => { window.localStorage.setItem(key, el.value); }); el.addEventListener('focus', () => { const savedVal = window.localStorage.getItem(key); if (savedVal && el.value === "") { let setter = Object.getOwnPropertyDescriptor(window.parent.HTMLInputElement.prototype, "value")?.set; if(el.tagName === 'TEXTAREA') setter = Object.getOwnPropertyDescriptor(window.parent.HTMLTextAreaElement.prototype, "value")?.set; if(setter) { setter.call(el, savedVal); el.dispatchEvent(new Event('input', { bubbles: true })); } else { el.value = savedVal; } } }); } }); } setInterval(initAutoSave, 1500); });</script>""", height=0, width=0)

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
    
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        current_data = load_json(DATA_FILE, {}) 
        if user_key not in current_data: current_data[user_key] = {}
        new_ans = {"a1_1": a1_1, "a1_2": a1_2, "a1_3": a1_3, "a2_1": a2_1, "a2_2_1": a2_2_1, "a2_2_2": a2_2_2, "a2_3_1": a2_3_1, "a2_3_2": a2_3_2, "a2_4": a2_4, "a3_1": a3_1, "a3_2": a3_2, "a3_3": a3_3, "a3_4": a3_4, "a3_5": a3_5, "a3_6": a3_6, "a3_7": a3_7, "a3_8": a3_8, "a3_9": a3_9, "a3_10": a3_10, "a3_11": a3_11}
        current_data[user_key][category] = new_ans
        save_json(DATA_FILE, current_data); create_auto_backup(f"[{u_name}] {category} 저장"); st.balloons(); st.success("🎉 저장 완료!")

def render_activity2_3th(user_key, u_info, current_role):
    category = ACT_3_2
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
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
    
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        current_data = load_json(DATA_FILE, {}) 
        if user_key not in current_data: current_data[user_key] = {}
        new_ans = {"q1_1": q1_1, "q1_2": q1_2, "q2_1": q2_1, "q2_2": q2_2, "q2_3": q2_3, "q3_1": q3_1, "q3_2": q3_2, "q3_3": q3_3, "q4_1": q4_1, "q4_2": q4_2, "q5_1": q5_1, "q5_2": q5_2, "q6_1": q6_1, "q6_2": q6_2, "q7_1": q7_1, "q7_2": q7_2, "q8_1": q8_1, "q8_2": q8_2}
        current_data[user_key][category] = new_ans
        save_json(DATA_FILE, current_data); create_auto_backup(f"[{u_name}] {category} 저장"); st.balloons(); st.success("🎉 저장 완료!")

def render_activity3_3th(user_key, u_info, current_role):
    category = ACT_3_3
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
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
    edited_s1_df = st.data_editor(s1_df, column_config={"관심도": st.column_config.SelectboxColumn("관심도", options=levels), "지식수준": st.column_config.SelectboxColumn("지식수준", options=levels)}, disabled=s1_dis, hide_index=True, use_container_width=True, key=f"s1_df_{category}")
    
    st.markdown("**2. (개인적 경험에 기반) 특정 국가에 대한 기억과 인상에 대한 분석**")
    st.caption("[직접 경험]")
    direct_df = pd.DataFrame(ans.get("direct_df", [{"여행해 본 국가": "", "해당 국가에 대한 구체적인 기억 혹은 인상": ""} for _ in range(3)]))
    edited_direct_df = st.data_editor(direct_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"direct_df_{category}")
    
    st.caption("[간접 경험]")
    ind1 = st.text_input("즐겨 보는 외국 영화/드라마는 어느 나라 작품?", value=ans.get("ind1", ""), disabled=disabled_flag, key=f"ind1_{category}")
    ind2 = st.text_input("좋아하는 음악가나 연예인이 있다면 어느 나라?", value=ans.get("ind2", ""), disabled=disabled_flag, key=f"ind2_{category}")
    ind3 = st.text_input("자주 먹는 외국 음식이 있다면 어느 나라?", value=ans.get("ind3", ""), disabled=disabled_flag, key=f"ind3_{category}")
    
    st.markdown("**3. 꼭 가보고 싶은 Top 5 국가와 그 이유**")
    top5_want = pd.DataFrame(ans.get("top5_want", [{"국가 혹은 지역": "", "이유": ""} for _ in range(5)]))
    edited_top5_want = st.data_editor(top5_want, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"top5_want_{category}")
    
    st.markdown("**4. 절대 가고 싶지 않은 Top 5 국가와 그 이유**")
    top5_notwant = pd.DataFrame(ans.get("top5_notwant", [{"국가 혹은 지역": "", "이유": ""} for _ in range(5)]))
    edited_top5_notwant = st.data_editor(top5_notwant, num_rows="fixed", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"top5_notwant_{category}")
    
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>2. 특정 대륙/국가에 대한 자신의 편견과 고정관념</h3>", unsafe_allow_html=True)
    st.markdown("**1. 국가별 한 단어 라벨링**")
    label_df = pd.DataFrame(ans.get("label_df", [{"가 보고 싶은 국가": "", "한 단어 라벨": "", "가고 싶지 않은 국가": "", "한 단어 라벨(부정)": ""} for _ in range(3)]))
    edited_label_df = st.data_editor(label_df, column_config={"한 단어 라벨(부정)": st.column_config.TextColumn("한 단어 라벨")}, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"label_df_{category}")
    
    st.markdown("**2. 개인적으로 가장 강한 편견을 가진 국가**")
    prej_df = pd.DataFrame(ans.get("prej_df", [{"국가명": "", "편견 내용": "", "편견 형성 과정 혹은 이유": ""} for _ in range(2)]))
    edited_prej_df = st.data_editor(prej_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"prej_df_{category}")
    
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
    edited_fake_df = st.data_editor(fake_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"fake_df_{category}")
    
    st.markdown("**5. 우월감이나 차별 의식 점검**")
    discrim_df = pd.DataFrame(ans.get("discrim_df", [{"어떤 국가에 대해?": "", "어떤 측면에서": "", "그 이유": ""} for _ in range(2)]))
    edited_discrim_df = st.data_editor(discrim_df, column_config={"그 이유": st.column_config.TextColumn("우월감이나 차별 의식을 느끼는 부분")}, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"discrim_df_{category}")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>3. 포용적이고 균형잡힌 세계관을 위한 노력</h3>", unsafe_allow_html=True)
    st.markdown("**1. 편견을 바꾸고 싶은 국가**")
    change_df = pd.DataFrame(ans.get("change_df", [{"어떤 국가에 대해?": "", "현재의 편견": "", "올바른 정보를 찾기 위한 계획": ""} for _ in range(2)]))
    edited_change_df = st.data_editor(change_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"change_df_{category}")
    
    st.markdown("**2. 가장 무관심했던 대륙 혹은 국가**")
    ignore_df = pd.DataFrame(ans.get("ignore_df", [{"선택 대륙/국가": "", "무관심 이유": "", "관심 확장을 위한 정보 수집 방법": ""} for _ in range(2)]))
    edited_ignore_df = st.data_editor(ignore_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"ignore_df_{category}")
    
    st.markdown("**3. 서구 중심적 시각에서 벗어나기**")
    western_df = pd.DataFrame(ans.get("western_df", [{"현재 가지고 있는 서구 중심적 시각": "", "개선 방법": ""} for _ in range(2)]))
    edited_western_df = st.data_editor(western_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"western_df_{category}")

    st.markdown("**4. 약소국 관점 이해하기**")
    weak_df = pd.DataFrame(ans.get("weak_df", [{"주목해 볼 국가": "", "그 이유": ""} for _ in range(2)]))
    edited_weak_df = st.data_editor(weak_df, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"weak_df_{category}")

    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>4. 목표로 하는 세계관</h3>", unsafe_allow_html=True)
    goal_1 = st.text_area("▶ 어떤 사람이 되고 싶은가?", value=ans.get("goal_1", ""), height=100, disabled=disabled_flag, key=f"goal_1_{category}")
    goal_2 = st.text_area("▶ 어떤 세계관을 갖고 싶은가?", value=ans.get("goal_2", ""), height=100, disabled=disabled_flag, key=f"goal_2_{category}")
    
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
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
            "western_df": edited_western_df.to_dict('records'), "weak_df": edited_weak_df.to_dict('records'),
            "goal_1": goal_1, "goal_2": goal_2
        }
        current_data[user_key][category] = new_ans
        save_json(DATA_FILE, current_data); create_auto_backup(f"[{u_name}] {category} 저장"); st.balloons(); st.success("🎉 저장 완료!")

def render_activity1_2nd(user_key, u_info, current_role):
    category = ACT_2_1
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
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
    edited_step3_df = st.data_editor(step3_df, column_config={"만족도 점수": st.column_config.SelectboxColumn("만족도 점수", options=["⭐", "⭐⭐", "⭐⭐⭐", "⭐⭐⭐⭐", "⭐⭐⭐⭐⭐"])}, num_rows="dynamic", use_container_width=True, hide_index=True, disabled=disabled_flag, key=f"step3_df_{category}")

    st.markdown("---")
    st.markdown("<h3 style='font-size: 24px; font-weight: 800; color: #111; margin-top: 30px; margin-bottom: 15px;'>Step 4. 우리의 방식으로 해 보는 울산 브랜딩: 정체성 리뉴얼</h3>", unsafe_allow_html=True)
    st.markdown("<div class='guide-box'><strong>밈과 지리적 사실의 융합을 통한 '울산성(Ulsan-ity)' 재정의</strong><br>STEP 1~3의 탐구 결과를 바탕으로, 울산의 프레임을 위트 있게 깨부수는 우리 모둠만의 울산 브랜딩 슬로건과 간단한 정책(시설)을 제안해 봅시다.</div>", unsafe_allow_html=True)
    step4_1 = st.text_input("1. 기존 프레임(대중의 오해)", value=ans.get("step4_1", ""), disabled=disabled_flag, key=f"step4_1_{category}")
    step4_2 = st.text_input("2. 기존 프레임에 대한 우리 모둠의 생각", value=ans.get("step4_2", ""), disabled=disabled_flag, key=f"step4_2_{category}")
    step4_3 = st.text_input("3. 기존 프레임에 대한 우리 모둠의 생각을 담은 강력한 슬로건", value=ans.get("step4_3", ""), disabled=disabled_flag, key=f"step4_3_{category}")
    step4_4 = st.text_area("4. 기존 프레임의 부정적인 부분을 상쇄할 수 있는 우리 모둠이 제안하는 울산의 거주 적합성 개선 아이디어", value=ans.get("step4_4", ""), disabled=disabled_flag, key=f"step4_4_{category}")

    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        current_data = load_json(DATA_FILE, {}) 
        if user_key not in current_data: current_data[user_key] = {}
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
        current_data[user_key][category] = new_ans
        save_json(DATA_FILE, current_data); create_auto_backup(f"[{u_name}] {category} 저장"); st.balloons(); st.success("🎉 저장 완료!")

def render_activity2_2nd(user_key, u_info, current_role):
    category = ACT_2_2
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    if current_role == "관리자":
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. (수정 가능)")

    st.markdown(f"<h2 style='font-size: 26px; font-weight: 900; color: #111; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ [2학년] 수행평가 2 - 내가 설계하는 N분 도시 with 파리의 15분 도시설계</h2>", unsafe_allow_html=True)
    
    if current_role == "학생":
        if disabled_flag:
            st.error(status_msg, icon="🔒")
        else:
            st.success(status_msg, icon="🔓")

    # 모둠원 정보 입력
    st.markdown("#### 👥 모둠 구성원 (학번/이름)")
    c1, c2, c3, c4 = st.columns(4)
    m1_id = c1.text_input("모둠원1(모둠장) 학번", value=ans.get("m1_id", ""), disabled=disabled_flag, key="m1_id_2_2")
    m1_name = c1.text_input("모둠원1 이름", value=ans.get("m1_name", ""), disabled=disabled_flag, key="m1_name_2_2")
    m2_id = c2.text_input("모둠원2 학번", value=ans.get("m2_id", ""), disabled=disabled_flag, key="m2_id_2_2")
    m2_name = c2.text_input("모둠원2 이름", value=ans.get("m2_name", ""), disabled=disabled_flag, key="m2_name_2_2")
    m3_id = c3.text_input("모둠원3 학번", value=ans.get("m3_id", ""), disabled=disabled_flag, key="m3_id_2_2")
    m3_name = c3.text_input("모둠원3 이름", value=ans.get("m3_name", ""), disabled=disabled_flag, key="m3_name_2_2")
    m4_id = c4.text_input("모둠원4 학번", value=ans.get("m4_id", ""), disabled=disabled_flag, key="m4_id_2_2")
    m4_name = c4.text_input("모둠원4 이름", value=ans.get("m4_name", ""), disabled=disabled_flag, key="m4_name_2_2")

    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ----------------------------------------------------
    # Step 1. 우리 동네 현황 진단
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 1. 우리 동네 현황 진단</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 20px;'>
        <b style='color: #2b6cb0;'>▶ 도보 15분 / 반경 1km 생활권 분석</b><br>
        <span style='font-size: 14px; color: #4a5568;'>: 실제 답사와 지도 앱 내용을 통한 필수 서비스 결손 현황 체크</span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("**1. 대상 지역** (예: 학교 주변 인근 OO아파트 OO단지 일대)")
    step1_1 = st.text_input("대상 지역 입력", value=ans.get("step1_1", ""), label_visibility="collapsed", disabled=disabled_flag, key="s1_1_2_2")

    st.markdown("<br>**2. 15분 생활권 반경 내 필수 서비스 체크리스트**", unsafe_allow_html=True)
    default_s1_df = [
        {"구분": "주거 및 생활", "필수 서비스 항목": "생필품 마트, 일상 편의시설", "충분": False, "부족 or 없음": False},
        {"구분": "의료 및 돌봄", "필수 서비스 항목": "병원, 약국, 돌봄센터", "충분": False, "부족 or 없음": False},
        {"구분": "노동 및 학습", "필수 서비스 항목": "청소년 무료 학습/스터디 공간", "충분": False, "부족 or 없음": False},
        {"구분": "여가 및 녹지", "필수 서비스 항목": "공원, 수변 공간, 휴식 공간", "충분": False, "부족 or 없음": False},
        {"구분": "교육 및 문화", "필수 서비스 항목": "도서관, 학습 공간, 문화 시설", "충분": False, "부족 or 없음": False},
        {"구분": "이동 및 보행", "필수 서비스 항목": "보행자 전용 도로, 자전거 도로", "충분": False, "부족 or 없음": False},
        {"구분": "", "필수 서비스 항목": "", "충분": False, "부족 or 없음": False},
        {"구분": "", "필수 서비스 항목": "", "충분": False, "부족 or 없음": False},
    ]
    s1_df_data = ans.get("step1_2_df", default_s1_df)
    s1_df = pd.DataFrame(s1_df_data)
    edited_step1_2_df = st.data_editor(
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
        key="s1_2_editor"
    )

    st.markdown("<br>**3. 선택한 지역의 핵심 문제점** *(반드시 실제 현장 답사 및 데이터에 기반한 내용을 작성할 것)*", unsafe_allow_html=True)
    
    col_p1, col_d1 = st.columns([1, 1])
    with col_p1:
        step1_p1 = st.text_area("문제점 1", value=ans.get("step1_p1", ans.get("step1_3_1", "")), height=100, disabled=disabled_flag, key="s1_p1")
    with col_d1:
        step1_d1 = st.text_area("데이터 1", value=ans.get("step1_d1", ""), height=100, disabled=disabled_flag, key="s1_d1")

    col_p2, col_d2 = st.columns([1, 1])
    with col_p2:
        step1_p2 = st.text_area("문제점 2", value=ans.get("step1_p2", ans.get("step1_3_2", "")), height=100, disabled=disabled_flag, key="s1_p2")
    with col_d2:
        step1_d2 = st.text_area("데이터 2", value=ans.get("step1_d2", ""), height=100, disabled=disabled_flag, key="s1_d2")

    col_p3, col_d3 = st.columns([1, 1])
    with col_p3:
        step1_p3 = st.text_area("문제점 3", value=ans.get("step1_p3", ans.get("step1_3_3", "")), height=100, disabled=disabled_flag, key="s1_p3")
    with col_d3:
        step1_d3 = st.text_area("데이터 3", value=ans.get("step1_d3", ""), height=100, disabled=disabled_flag, key="s1_d3")

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
        : 기본 100포인트 부여, 포인트를 활용하여 기존의 비효율적, 차량 중심 공간을 보행자를 위한 친환경 인프라로!!<br>
        : 새롭게 추가하는 카테고리/코드/세부 개조 항목 관련한 포인트는 최소 10pt, 최대 20pt(10~20pt)<br>
        : 포인트는 남김 없이 모두 사용해야 함<br>
        : 최소한의 현실 가능성은 충족할 것 예) 지하철 개통, 공항 건설... ㅠ.ㅠ
        </span>
    </div>
    """, unsafe_allow_html=True)

    # [이 부분을 드래그하여 삭제] (930번 ~ 969번 줄)
    step2_table_html = """
    <table style="width: 100%; border-collapse: collapse; margin-top: 10px; margin-bottom: 25px; font-size: 14px; background-color: #ffffff;">
        <thead>
            <tr style="background-color: #f8f9fa;">
                <th style="padding: 10px; border: 1px solid #dee2e6; text-align: center; font-weight: 700; width: 26%;">카테고리</th>
                <th style="padding: 10px; border: 1px solid #dee2e6; text-align: center; font-weight: 700; width: 10%;">코드</th>
                <th style="padding: 10px; border: 1px solid #dee2e6; text-align: center; font-weight: 700; width: 50%;">세부 개조 항목</th>
                <th style="padding: 10px; border: 1px solid #dee2e6; text-align: center; font-weight: 700; width: 14%;">비용</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td rowspan="6" style="text-align: center; vertical-align: middle; font-weight: 700; background-color: #fafbfc; border: 1px solid #dee2e6;">안전한 보행 환경</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">A-1</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">여고생 안심 하교길 스마트 로드</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-15pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">A-2</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">아파트 단지 간 담장 철거 및 공공 보행로 연결</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-20pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">A-3</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">차로 축소 및 쾌적한 보행을 위한 녹지 공간 조성</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-20pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">A-4</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">스마트 횡단보도 및 교통약자/학생 쉼터</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-10pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">A-5</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;"></td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;"></td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">A-6</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;"></td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;"></td>
            </tr>
            <tr>
                <td rowspan="5" style="text-align: center; vertical-align: middle; font-weight: 700; background-color: #fafbfc; border: 1px solid #dee2e6;">녹지 및 생태공간 구축</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">B-1</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">아파트 상가/방치 공터 → 도심 소공원 조성</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-15pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">B-2</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">도심 바람길 숲 및 수변 산책로 조성</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-15pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">B-3</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">에코 펫파크(반려견 전용 공원 및 산책로)</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-15pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">B-4</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;"></td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;"></td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">B-5</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;"></td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;"></td>
            </tr>
            <tr>
                <td rowspan="5" style="text-align: center; vertical-align: middle; font-weight: 700; background-color: #fafbfc; border: 1px solid #dee2e6;">문화와 교육을 위한 공간</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">C-1</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">24시간 공공 스터디 & 커뮤니티 카페</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-15pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">C-2</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">청소년 팝업 스튜디오 & 소공연장</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-15pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">C-3</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">친환경 스마트 팜</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-10pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">C-4</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;"></td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;"></td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">C-5</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;"></td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;"></td>
            </tr>
            <tr>
                <td rowspan="4" style="text-align: center; vertical-align: middle; font-weight: 700; background-color: #fafbfc; border: 1px solid #dee2e6;">효율적인 교통과 모빌리티 구축</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">D-1</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">공유 자전거 및 킥보드 전용 도로</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-15pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">D-2</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;">스마트 버스 쉘터(공기 청정, 냉난방 설비 구축)</td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">-10pt</td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">D-3</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;"></td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;"></td>
            </tr>
            <tr>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;">D-4</td>
                <td style="padding: 8px 12px; border: 1px solid #dee2e6;"></td>
                <td style="text-align: center; font-weight: 600; padding: 8px; border: 1px solid #dee2e6;"></td>
            </tr>
        </tbody>
    </table>
    """
    st.markdown(step2_table_html, unsafe_allow_html=True)

    # 신규 추가 개조 항목 입력칸
    st.markdown("##### ✏️ 모둠 신규 추가 개조 항목 작성 (필요 시 작성)")
    default_custom_df = [
        {"코드": "A-5", "세부 개조 항목": "", "비용(10~20pt)": ""},
        {"코드": "B-4", "세부 개조 항목": "", "비용(10~20pt)": ""},
        {"코드": "C-4", "세부 개조 항목": "", "비용(10~20pt)": ""},
        {"코드": "D-3", "세부 개조 항목": "", "비용(10~20pt)": ""},
    ]
    custom_items_data = ans.get("step2_custom_df", default_custom_df)
    custom_df = pd.DataFrame(custom_items_data)
    edited_custom_df = st.data_editor(
        custom_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=disabled_flag,
        column_config={
            "코드": st.column_config.TextColumn("코드(예: A-5, B-4)", width="small"),
            "세부 개조 항목": st.column_config.TextColumn("세부 개조 항목", width="large"),
            "비용(10~20pt)": st.column_config.TextColumn("비용", width="small"),
        },
        key="s2_custom_editor"
    )

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

    st.markdown("**3. 한정된 100pt를 활용해 무엇을 버리고 무엇을 채웠는가? 그 이유는 무엇인가?**")
    step4_3 = st.text_area("3. 버리고 채운 것과 이유", value=ans.get("step4_3", ""), height=100, label_visibility="collapsed", disabled=disabled_flag, key="s4_3_input")

    st.markdown("**4. 공간 재설계로 인해 일상이 어떻게 변화할 것이라고 생각하는가?**")
    step4_4 = st.text_area("4. 일상의 변화", value=ans.get("step4_4", ""), height=100, label_visibility="collapsed", disabled=disabled_flag, key="s4_4_input")

    # ----------------------------------------------------
    # 저장하기 버튼 & 데이터베이스 동기화
    # ----------------------------------------------------
    st.markdown("<br>", unsafe_allow_html=True)
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        current_data = load_json(DATA_FILE, {})
        if user_key not in current_data:
            current_data[user_key] = {}
        
        new_ans = {
            "m1_id": m1_id, "m1_name": m1_name, "m2_id": m2_id, "m2_name": m2_name,
            "m3_id": m3_id, "m3_name": m3_name, "m4_id": m4_id, "m4_name": m4_name,
            "step1_1": step1_1,
            "step1_2_df": edited_step1_2_df.to_dict('records'),
            "step1_p1": step1_p1, "step1_d1": step1_d1,
            "step1_p2": step1_p2, "step1_d2": step1_d2,
            "step1_p3": step1_p3, "step1_d3": step1_d3,
            "step2_point_df": edited_step2_point_df.to_dict('records'),
            "step4_1": step4_1,
            "step4_2": step4_2,
            "step4_3": step4_3,
            "step4_4": step4_4
        }
        
        current_data[user_key][category] = new_ans
        save_json(DATA_FILE, current_data)
        create_auto_backup(f"[{u_name}] {category} 저장")
        st.balloons()
        st.success("성공적으로 저장되었습니다!")

def render_activity3_2nd(user_key, u_info, current_role):
    category = ACT_2_3
    u_name, u_id, u_subj, user_class = u_info.get("name", ""), u_info.get("id", ""), u_info.get("subject", "전체"), u_info.get("class_group", "")
    learning_data = load_json(DATA_FILE, {})
    owner_key, ans = get_user_activity_data(user_key, u_id, u_subj, user_class, category, learning_data)
    
    is_active, status_msg = check_active_with_exception(category, user_class, user_key)
    disabled_flag = (current_role == "학생" and not is_active)
    if current_role == "관리자":
        disabled_flag = False
        st.info("💡 교사/관리자 모드입니다. (수정 가능)")

    st.markdown("<h2 style='font-size: 26px; font-weight: 900; color: #111; padding-bottom: 10px; border-bottom: 2px solid #ccc; margin-bottom: 20px;'>♣ [2학년] 수행평가 3 - 도시 미디어 파사드 기획 및 스토리보드 제작</h2>", unsafe_allow_html=True)
    
    if current_role == "학생":
        if disabled_flag:
            st.error(status_msg, icon="🔒")
        else:
            st.success(status_msg, icon="🔓")

    # 개별 정보
    c1, c2, c3 = st.columns([1, 1, 2])
    ind_id = c1.text_input("학번", value=ans.get("ind_id", u_id), disabled=disabled_flag, key="s3_ind_id")
    ind_name = c2.text_input("이름", value=ans.get("ind_name", u_name), disabled=disabled_flag, key="s3_ind_name")
    ind_career = c3.text_input("희망 진로", value=ans.get("ind_career", ""), disabled=disabled_flag, key="s3_ind_career")

    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ----------------------------------------------------
    # Step 1. 우리 지역의 정체성 탐색
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 1. 우리 지역의 정체성 탐색</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 15px;'>
        <b style='color: #2b6cb0;'>▶ 우리 지역의 정체성은 '느낌'이 아니라 '근거'에서 출발합니다. 아래 세 개의 축에서 키워드를 뽑고, 반드시 출처가 있는 자료로 뒷받침하세요.</b><br>
        <span style='font-size: 13.5px; color: #4a5568;'>
        ▶ 내 작품의 소재가 될 지역 정체성을 세 개의 축에서 찾고, 반드시 근거 자료와 출처를 함께 적습니다.<br>
        ▶ <b>추천 검색어:</b> '울산광역시 통계포털', 'KOSIS 지역별 고용조사', '국가문화유산포털', '카카오맵/네이버맵 지적편집도'<br>
        <span style='color: #c53030; font-weight: bold;'>※ 근거 자료와 출처가 비어 있는 칸은 점수로 인정되지 않습니다.</span>
        </span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style='background-color: #fefce8; border: 1px solid #fde047; border-left: 4px solid #ca8a04; border-radius: 6px; padding: 10px 14px; font-size: 14.5px; color: #1e293b; line-height: 1.6; margin-bottom: 12px;'>
        <b style='color: #b45309; font-size: 15px;'>💡 [작성 예시]</b><br>
        • <b>구분:</b> (예시) 자연·생태 &nbsp;|&nbsp; 
        • <b>키워드:</b> 죽음의 강에서 되살아난 태화강<br>
        • <b>근거:</b> 수질이 생태 등급으로 회복, 철새 서식지로 지정 &nbsp;|&nbsp; 
        • <b>출처:</b> 울산시 환경 관련 통계 / 20OO
    </div>
    """, unsafe_allow_html=True)

    default_s1_df = [
        {"구분": "1. 자연·생태", "내가 찾은 정체성 키워드 혹은 문장": "", "근거가 되는 사실·통계·사건": "", "출처 (기관명 / 자료명 / 연도)": ""},
        {"구분": "2. 산업·경제", "내가 찾은 정체성 키워드 혹은 문장": "", "근거가 되는 사실·통계·사건": "", "출처 (기관명 / 자료명 / 연도)": ""},
        {"구분": "3. 역사·문화", "내가 찾은 정체성 키워드 혹은 문장": "", "근거가 되는 사실·통계·사건": "", "출처 (기관명 / 자료명 / 연도)": ""},
    ]
    s1_data = ans.get("step1_df", default_s1_df)
    s1_df = pd.DataFrame(s1_data)
    edited_step1_df = st.data_editor(
        s1_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=disabled_flag,
        column_config={
            "구분": st.column_config.TextColumn("구분", width="medium"),
            "내가 찾은 정체성 키워드 혹은 문장": st.column_config.TextColumn("내가 찾은 정체성 키워드 혹은 문장", width="large"),
            "근거가 되는 사실·통계·사건": st.column_config.TextColumn("근거가 되는 사실·통계·사건", width="large"),
            "출처 (기관명 / 자료명 / 연도)": st.column_config.TextColumn("출처 (기관명 / 자료명 / 연도)", width="medium"),
        },
        key="s1_df_editor_2_3"
    )

    st.markdown("<br>**내가 최종 선택한 핵심 키워드 혹은 문장**", unsafe_allow_html=True)
    step1_keyword = st.text_input("최종 선택 키워드", value=ans.get("step1_keyword", ""), label_visibility="collapsed", disabled=disabled_flag, key="s1_keyword_2_3")

    st.markdown("**내 작품이 전할 단 하나의 메시지** *(한 문장으로 쓸 것. 예: \"울산은 공장의 도시가 아니라, 공장과 철새가 함께 사는 도시다.\")*")
    step1_message = st.text_area("단 하나의 메시지", value=ans.get("step1_message", ""), height=80, label_visibility="collapsed", disabled=disabled_flag, key="s1_message_2_3")

    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ----------------------------------------------------
    # Step 2. 캔버스 선정 — 어떤 건축물에 어떤 형태의 빛을 입힐 것인가?
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 2. 캔버스 선정 — 어떤 건축물에 어떤 형태의 빛을 입힐 것인가?</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 15px;'>
        <span style='font-size: 13.5px; color: #4a5568;'>
        ▶ 우리 지역의 실제 건축물·구조물 3 곳을 후보로 조사하고 비교한 뒤 최종 1 곳을 선정합니다.<br>
        ▶ 직접 답사하거나 지도 로드뷰로 확인한 내용을 적습니다. <b>상상으로 쓴 내용은 인정되지 않습니다.</b><br>
        <i>(현장 답사가 어려운 경우 지도 앱의 로드뷰·위성사진으로 대체하되, 캡처 화면을 반드시 첨부할 것)</i>
        </span>
    </div>
    """, unsafe_allow_html=True)

    default_s2_df = [
        {"검토 항목": "건물명", "후보 1": "", "후보 2": "", "후보 3": ""},
        {"검토 항목": "벽면 조건\n(면적·재질·창문 비율)", "후보 1": "", "후보 2": "", "후보 3": ""},
        {"검토 항목": "관람 조건\n(관람 거리, 정면성, 야간 유동인구)", "후보 1": "", "후보 2": "", "후보 3": ""},
        {"검토 항목": "접근성\n(대중교통·주차·야간)", "후보 1": "", "후보 2": "", "후보 3": ""},
        {"검토 항목": "예상되는 제약\n(빛공해, 주거지 인접, 관리 주체)", "후보 1": "", "후보 2": "", "후보 3": ""},
        {"검토 항목": "지역 정체성과의 연관성", "후보 1": "", "후보 2": "", "후보 3": ""},
        {"검토 항목": "적합도 (☆ 5점 만점)", "후보 1": "☆☆☆☆☆", "후보 2": "☆☆☆☆☆", "후보 3": "☆☆☆☆☆"},
    ]
    s2_data = ans.get("step2_matrix_df", default_s2_df)
    s2_df = pd.DataFrame(s2_data)
    edited_step2_df = st.data_editor(
        s2_df,
        num_rows="fixed",
        use_container_width=True,
        hide_index=True,
        disabled=disabled_flag,
        column_config={
            "검토 항목": st.column_config.TextColumn("검토 항목", width="medium"),
            "후보 1": st.column_config.TextColumn("후보 1", width="large"),
            "후보 2": st.column_config.TextColumn("후보 2", width="large"),
            "후보 3": st.column_config.TextColumn("후보 3", width="large"),
        },
        key="s2_matrix_editor_2_3"
    )

    st.markdown("<br>**최종 선정 건물**", unsafe_allow_html=True)
    step2_final_building = st.text_input("최종 선정 건물", value=ans.get("step2_final_building", ""), label_visibility="collapsed", disabled=disabled_flag, key="s2_final_bldg")

    st.markdown("**이유** *(Step 1 의 정체성 키워드 혹은 문장과 연결하여 서술할 것)*")
    step2_reason = st.text_area("선정 이유", value=ans.get("step2_reason", ""), height=90, label_visibility="collapsed", disabled=disabled_flag, key="s2_reason_input")

    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ----------------------------------------------------
    # Step 3. 주어진 조건 진단 및 대응 설계
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 3. 주어진 조건 진단 및 대응 설계</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 15px;'>
        <span style='font-size: 13.5px; color: #4a5568;'>
        ▶ 내가 선정한 건물과 그 주변에는 내가 바꿀 수 없는 조건들이 이미 존재합니다.<br>
        ▶ 조건을 없애거나 무시하는 것이 아니라, 그 조건을 그대로 받아들인 상태에서 어떻게 작품을 성립시킬지 설계합니다.<br>
        <b style='color: #c53030;'>※ 중요: 조건을 '문제점'으로만 적고 끝내면 점수를 받지 못합니다. 반드시 「나의 대응 방안」까지 채워야 합니다.</b><br>
        <span style='color: #2b6cb0;'>※ 제약을 오히려 작품의 조형 요소로 뒤집어 활용한 경우 가장 높은 평가를 받습니다.</span>
        </span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style='background-color: #fefce8; border: 1px solid #fde047; border-left: 4px solid #ca8a04; border-radius: 6px; padding: 10px 14px; font-size: 14.5px; color: #1e293b; line-height: 1.6; margin-bottom: 12px;'>
        <b style='color: #b45309; font-size: 15px;'>💡 [작성 예시]</b><br>
        • <b>조건 영역:</b> (예시) 물리적 조건 &nbsp;|&nbsp; 
        • <b>현장의 실제 조건:</b> 외벽의 40%가 창문이라 영상이 끊겨 보인다<br>
        • <b>작품에 미치는 영향:</b> 인물이나 글자를 크게 넣으면 형태가 깨진다<br>
        • <b>나의 대응 방안:</b> 창틀 격자를 공장 창문으로 역이용해, 격자 사이로 빛이 번지는 산업 도시 이미지를 연출한다
    </div>
    """, unsafe_allow_html=True)
    default_s3_df = [
        {"조건 영역": "1. 물리적 조건\n(벽면 형태·재질·구조물)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "2. 빛·환경 조건\n(주변 조명·간판·빛공해)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "3. 시간·계절 조건\n(일몰 시각·강수·바람)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "4. 주민·이웃 조건\n(인접 주거·상가·소음)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "5. 행정·비용 조건\n(허가·예산·관리 주체)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
        {"조건 영역": "6. 접근·안전 조건\n(동선·차도·배리어프리)", "현장의 실제 조건 (확인한 사실)": "", "작품에 미치는 영향": "", "나의 대응 방안": ""},
    ]
    s3_data = ans.get("step3_df", default_s3_df)
    s3_df = pd.DataFrame(s3_data)
    edited_step3_df = st.data_editor(
        s3_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=disabled_flag,
        column_config={
            "조건 영역": st.column_config.TextColumn("조건 영역", width="medium"),
            "현장의 실제 조건 (확인한 사실)": st.column_config.TextColumn("현장의 실제 조건 (확인한 사실)", width="large"),
            "작품에 미치는 영향": st.column_config.TextColumn("작품에 미치는 영향", width="large"),
            "나의 대응 방안": st.column_config.TextColumn("나의 대응 방안", width="large"),
        },
        key="s3_editor_2_3"
    )

    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ----------------------------------------------------
    # Step 4. 작품 스토리보드 4컷 & 미디어 파사드 시뮬레이션 영상
    # ----------------------------------------------------
    st.markdown("### Step 4. 작품 스토리보드 4컷 & 미디어 파사드 시뮬레이션 영상")
    st.markdown("""
    > **안내사항**  
    > • 도입-전개-절정-마무리 4컷의 **스케치 파일**과 **장면 설명**을 작성합니다.  
    > • 4컷 등록 후, 하단의 **[🎬 4컷 연결 시뮬레이션 영상 자동 생성]** 버튼을 누르거나 직접 편집한 영상(MP4/GIF)을 등록하세요.
    """)

    cut_names = ["1 도입", "2 전개", "3 절정", "4 마무리"]
    saved_step4 = ans.get("step4_df", [])
    step4_data = []
    current_images = []

    for i, c_name in enumerate(cut_names):
        old_cut = saved_step4[i] if i < len(saved_step4) and isinstance(saved_step4[i], dict) else {}
        old_img = old_cut.get("스케치_img", "")
        old_desc = old_cut.get("장면 설명 · 사용 기술 · 소요 시간", "")

        st.markdown(f"#### 🎬 {c_name}")
        col_img, col_txt = st.columns([1, 1.2])

        with col_img:
            up_file = st.file_uploader(
                f"[{c_name}] 스케치 파일 (JPG/PNG)", 
                type=["png", "jpg", "jpeg", "webp"], 
                key=f"step4_file_{i}",
                disabled=disabled_flag if 'disabled_flag' in locals() else False
            )
            current_img = process_sketch_image(up_file) if up_file else old_img
            current_images.append(current_img)

            if current_img:
                st.image(current_img, caption=f"{c_name} 스케치", use_container_width=True)
            else:
                st.caption("📷 등록된 스케치 이미지가 없습니다.")

        with col_txt:
            desc_val = st.text_area(
                f"[{c_name}] 장면 설명 · 사용 기술 · 소요 시간",
                value=old_desc,
                height=170,
                key=f"step4_desc_{i}",
                disabled=disabled_flag if 'disabled_flag' in locals() else False
            )

        st.markdown("<hr style='margin: 12px 0; border: 0.5px dashed #cbd5e1;'>", unsafe_allow_html=True)
        step4_data.append({
            "컷": c_name,
            "스케치_img": current_img,
            "장면 설명 · 사용 기술 · 소요 시간": desc_val
        })

    # --- [미디어 파사드 시뮬레이션 영상 영역] ---
    st.markdown("#### 🎥 최종 미디어 파사드 시뮬레이션 영상")
    saved_video = ans.get("step4_video", "")

    v_col1, v_col2 = st.columns([1, 1])
    with v_col1:
        st.markdown("**방법 A. 4컷 스케치 자동 연결**")
        if st.button("🪄 4컷 스케치 연결 영상(움짤) 자동 생성", key="btn_gen_gif", disabled=disabled_flag if 'disabled_flag' in locals() else False):
            valid_imgs = [img for img in current_images if img]
            if len(valid_imgs) < 2:
                st.warning("⚠️ 최소 2개 이상의 스케치 이미지를 업로드해야 영상을 생성할 수 있습니다.")
            else:
                with st.spinner("4컷을 연결하여 시뮬레이션 영상을 생성 중입니다..."):
                    saved_video = create_storyboard_gif(valid_imgs, duration=1500)
                    st.success("🎉 시뮬레이션 영상이 생성되었습니다! 하단 [저장하기]를 눌러 반영하세요.")

    with v_col2:
        st.markdown("**방법 B. 직접 제작한 영상(MP4/GIF) 업로드**")
        up_video = st.file_uploader(
            "편집 영상 업로드 (MP4, WebM, GIF / 최대 10MB)", 
            type=["mp4", "webm", "gif"], 
            key="step4_video_uploader",
            disabled=disabled_flag if 'disabled_flag' in locals() else False
        )
        if up_video is not None:
            saved_video = process_uploaded_video(up_video)

    # 영상 미리보기 출력
    if saved_video:
        st.markdown("**▶ 등록된 시뮬레이션 영상 미리보기**")
        if "data:video" in saved_video:
            st.video(saved_video)
        else:
            st.image(saved_video, caption="미디어 파사드 4컷 모션 시뮬레이션", width=500)
    else:
        st.info("ℹ️ 아직 등록된 시뮬레이션 영상이 없습니다. [자동 생성] 버튼을 누르거나 직접 만든 영상 파일을 올려주세요.")

    st.markdown("---")
    # ----------------------------------------------------
    # Step 5. 작품 설명 카드 작성 및 갤러리 워크
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 5. 작품 설명 카드 작성 및 갤러리 워크</h3>", unsafe_allow_html=True)
    st.markdown("""
    <div style='background-color: #f0f7ff; border-left: 4px solid #3182ce; padding: 12px 16px; border-radius: 4px; margin-bottom: 20px;'>
        <span style='font-size: 13.5px; color: #4a5568;'>
        ▶ 완성한 작품을 전시장에 걸 때 옆에 붙는 캡션 패널을 직접 씁니다. 교실 벽에 게시하여 서로 감상합니다.
        </span>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("**작품 제목** *(관람객의 눈길을 끌 수 있도록. 부제를 붙여도 좋다.)*")
    step5_title = st.text_input("작품 제목", value=ans.get("step5_title", ""), label_visibility="collapsed", disabled=disabled_flag, key="s5_title")

    st.markdown("**전시 장소** *(건물명 / 투사 벽면 / 권장 관람 위치)*")
    step5_place = st.text_input("전시 장소", value=ans.get("step5_place", ""), label_visibility="collapsed", disabled=disabled_flag, key="s5_place")

    st.markdown("**작품 개요** *(5 문장 이내)*")
    step5_desc = st.text_area("작품 개요", value=ans.get("step5_desc", ""), height=90, label_visibility="collapsed", disabled=disabled_flag, key="s5_desc")

    st.markdown("**이 작품이 지역의 어떤 정체성을 담았는가** *(Step 1 의 근거 자료를 인용하여 쓸 것)*")
    step5_identity = st.text_area("지역 정체성 반영", value=ans.get("step5_identity", ans.get("step5_q1", "")), height=90, label_visibility="collapsed", disabled=disabled_flag, key="s5_identity")

    st.markdown("**현장 조건을 어떻게 작품에 반영했는가** *(Step 3 에서 가장 잘 해결한 조건 1 가지를 골라 쓸 것)*")
    step5_condition = st.text_area("현장 조건 반영", value=ans.get("step5_condition", ans.get("step5_q2", "")), height=90, label_visibility="collapsed", disabled=disabled_flag, key="s5_condition")

    st.markdown("**이 작품이 우리 지역에 남길 변화** *(관람객 / 주민 / 상권 세 측면에서 각각 한 줄씩)*")
    
    # 3개 구역 분할 (관람객 / 주민 / 상권)
    col_c1, col_c2, col_c3 = st.columns(3)
    
    with col_c1:
        st.markdown("##### 👥 1. 관람객 측면")
        step5_change_visitor = st.text_area(
            "관람객에게 미칠 영향 및 변화",
            value=ans.get("step5_change_visitor", ""),
            placeholder="예: 우리 지역의 새로운 문화적 랜드마크로 인식하고 야간 명소로 방문하게 된다.",
            height=110,
            key="step5_change_vis_input",
            disabled=disabled_flag if 'disabled_flag' in locals() else False,
            label_visibility="collapsed"
        )

    with col_c2:
        st.markdown("##### 🏡 2. 주민 측면")
        step5_change_resident = st.text_area(
            "지역 주민에게 미칠 영향 및 변화",
            value=ans.get("step5_change_resident", ""),
            placeholder="예: 낡고 어두웠던 공간이 밝아져 자긍심을 느끼고 야간 안심 귀갓길이 형성된다.",
            height=110,
            key="step5_change_res_input",
            disabled=disabled_flag if 'disabled_flag' in locals() else False,
            label_visibility="collapsed"
        )

    with col_c3:
        st.markdown("##### 🛍️ 3. 상권 측면")
        step5_change_market = st.text_area(
            "주변 상권에게 미칠 영향 및 변화",
            value=ans.get("step5_change_market", ""),
            placeholder="예: 야간 유동 인구가 증가하여 인근 카페, 식당 등 골목 상권이 활성화된다.",
            height=110,
            key="step5_change_mkt_input",
            disabled=disabled_flag if 'disabled_flag' in locals() else False,
            label_visibility="collapsed"
        )
    st.markdown("<hr style='margin: 30px 0;'>", unsafe_allow_html=True)

    # ----------------------------------------------------
    # Step 6. 제출 전 자기 점검 및 활용 기록
    # ----------------------------------------------------
    st.markdown("<h3 style='font-size: 22px; font-weight: 800; color: #111;'>Step 6. 제출 전 자기 점검 및 활용 기록</h3>", unsafe_allow_html=True)
    
    default_chk_df = [
        {"No": 1, "점검 항목": "Step 1 의 정체성 키워드 3개 모두에 출처를 적었다.", "확인": False},
        {"No": 2, "점검 항목": "후보 건축물 3 곳을 실제로 답사하거나 로드뷰로 확인했다.", "확인": False},
        {"No": 3, "점검 항목": "Step 3 의 6개 조건 영역을 빈칸 없이 채웠다.", "확인": False},
        {"No": 4, "점검 항목": "조건을 문제점으로만 쓰지 않고, 대응 방안까지 모두 적었다.", "확인": False},
        {"No": 5, "점검 항목": "스토리보드 4 컷이 Step 1 의 메시지와 연결되어 있다.", "확인": False},
        {"No": 6, "점검 항목": "진로 심화 트랙 산출물을 함께 제출했다.", "확인": False},
        {"No": 7, "점검 항목": "작품 설명 카드에 근거 자료를 인용했다.", "확인": False},
    ]
    chk_data = ans.get("step6_chk_df", default_chk_df)
    chk_df = pd.DataFrame(chk_data)
    edited_step6_chk_df = st.data_editor(
        chk_df,
        num_rows="fixed",
        use_container_width=True,
        hide_index=True,
        disabled=disabled_flag,
        column_config={
            "No": st.column_config.NumberColumn("No", width="small"),
            "점검 항목": st.column_config.TextColumn("점검 항목", width="large"),
            "확인": st.column_config.CheckboxColumn("확인", width="small"),
        },
        key="s6_chk_editor"
    )

    st.markdown("<br>**▶ 생성형 AI 활용 기록** *(이미지·아이디어 생성에 AI 를 사용한 경우 반드시 기록. 미기재 시 평가에서 제외됩니다.)*", unsafe_allow_html=True)
    default_ai_df = [
        {"사용한 도구명": "", "입력한 프롬프트": "", "AI 결과물을 내가 수정·판단한 내용": ""},
        {"사용한 도구명": "", "입력한 프롬프트": "", "AI 결과물을 내가 수정·판단한 내용": ""},
    ]
    ai_data = ans.get("step6_ai_df", default_ai_df)
    ai_df = pd.DataFrame(ai_data)
    edited_step6_ai_df = st.data_editor(
        ai_df,
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        disabled=disabled_flag,
        column_config={
            "사용한 도구명": st.column_config.TextColumn("사용한 도구명", width="medium"),
            "입력한 프롬프트": st.column_config.TextColumn("입력한 프롬프트", width="large"),
            "AI 결과물을 내가 수정·판단한 내용": st.column_config.TextColumn("AI 결과물을 내가 수정·판단한 내용", width="large"),
        },
        key="s6_ai_editor"
    )

    st.markdown("<br>**활동 성찰** *(이 활동으로 도시와 나의 진로에 대해 새로 알게 된 점)*", unsafe_allow_html=True)
    step6_reflection = st.text_area("활동 성찰", value=ans.get("step6_reflection", ""), height=100, label_visibility="collapsed", disabled=disabled_flag, key="s6_reflection")

    # ----------------------------------------------------
    # 저장하기 버튼
    # ----------------------------------------------------
    st.markdown("<br>", unsafe_allow_html=True)
    if not disabled_flag and st.button("저장하기", type="primary", key=f"save_{category}"):
        current_data = load_json(DATA_FILE, {})
        if user_key not in current_data:
            current_data[user_key] = {}
        
        new_ans = {
            "ind_id": ind_id, "ind_name": ind_name, "ind_career": ind_career,
            "step1_df": edited_step1_df.to_dict('records'),
            "step1_keyword": step1_keyword,
            "step1_message": step1_message,
            "step2_matrix_df": edited_step2_df.to_dict('records'),
            "step2_final_building": step2_final_building,
            "step2_reason": step2_reason,
            "step3_df": edited_step3_df.to_dict('records'),
            "step4_df": step4_data,
            "step4_video": saved_video,
            "step5_title": step5_title,
            "step5_place": step5_place,
            "step5_desc": step5_desc,
            "step5_identity": step5_identity,
            "step5_condition": step5_condition,
            "step5_change_visitor": step5_change_visitor,
            "step5_change_resident": step5_change_resident,
            "step5_change_market": step5_change_market,
            "step5_change": f"[관람객] {step5_change_visitor} / [주민] {step5_change_resident} / [상권] {step5_change_market}",
            "step6_chk_df": edited_step6_chk_df.to_dict('records'),
            "step6_ai_df": edited_step6_ai_df.to_dict('records'),
            "step6_reflection": step6_reflection
        }
        
        current_data[user_key][category] = new_ans
        save_json(DATA_FILE, current_data)
        create_auto_backup(f"[{u_name}] {category} 저장")
        st.balloons()
        st.success("성공적으로 저장되었습니다!")

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
            new_ans["step4_df"] = step4_data    
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

components.html("""<script>document.addEventListener("DOMContentLoaded", function() { const parentDoc = window.parent.document; function initAutoSave() { const elements = parentDoc.querySelectorAll('input[type="text"], textarea'); elements.forEach(el => { const ariaLabel = el.getAttribute('aria-label') || ''; const key = 'autosave_' + window.parent.location.pathname + '_' + ariaLabel; if (!el.dataset.autosaveAttached && ariaLabel !== '') { el.dataset.autosaveAttached = "true"; el.addEventListener('input', () => { window.localStorage.setItem(key, el.value); }); el.addEventListener('focus', () => { const savedVal = window.localStorage.getItem(key); if (savedVal && el.value === "") { let setter = Object.getOwnPropertyDescriptor(window.parent.HTMLInputElement.prototype, "value")?.set; if(el.tagName === 'TEXTAREA') setter = Object.getOwnPropertyDescriptor(window.parent.HTMLTextAreaElement.prototype, "value")?.set; if(setter) { setter.call(el, savedVal); el.dispatchEvent(new Event('input', { bubbles: true })); } else { el.value = savedVal; } } }); } }); } setInterval(initAutoSave, 1500); });</script>""", height=0, width=0)

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
            if not has_individual_dl: st.info("아직 제출한 활동지가 없습니다.")
            st.markdown("---")
            html_content_all = generate_portfolio_html(current_user_key, u_info, u_info['subject'], app_config, learning_data)
            st.download_button(label=f"📦 {u_info['name']} 학생 전체 포트폴리오 일괄 다운로드 (웹문서)", data=html_content_all.encode('utf-8-sig'), file_name=f"{u_info['name']}_전체_포트폴리오.html", mime="text/html", type="primary")

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
                            opt_stu = ["선택"] + list(filt_stu.keys())
                            sel_stu = st.selectbox("학생 선택", opt_stu, format_func=lambda x: "선택" if x == "선택" else f"[{filt_stu[x].get('class_group')}] {filt_stu[x].get('name')} ({filt_stu[x].get('id')})", key="sel_stu_tab0")
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
                st.info("💡 학생들의 데이터가 제출되거나 관리자 설정이 변경될 때마다 안전한 스냅샷이 실시간으로 생성됩니다. 과거 특정 시점으로 되돌리려면 아래 목록에서 선택 후 복원 버튼 누르세요.")
                
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
