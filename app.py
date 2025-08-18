# app.py
# -*- coding: utf-8 -*-
"""
ЭЕШ Математик — Бэлтгэл ба Жишиг тест (Streamlit)
- Бэлтгэлийн арга зүй (чеклист, зөвлөгөө)
- 4 хувилбар × 40 бодлого (MCQ + тоон хариу)
- 100 минутын таймер, явцын самбар
- Автомат үнэлгээ, сэдвийн тайлан, CSV/PDF татах
- CSV/JSON-оор асуултын сан импортлох боломжтой, импорт хийгдээгүй бол демо сан автоматаар үүсгэнэ

Ажиллуулах:
  1) pip install streamlit pandas numpy reportlab
  2) python -m streamlit run app.py

CSV/JSON формат:
  variant,qnum,type,question,A,B,C,D,correct,score,solution,topic,difficulty,tolerance
  type: mcq | num ; correct: (A/B/C/D) эсвэл тоон утга; tolerance: num-д зөвшөөрөх алдаа

© 2025
"""

import io
import json
import math
import os
import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import streamlit as st
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.lib import colors

# ---------------------- CONFIG ----------------------
st.set_page_config(
    page_title="ЭЕШ Математик — Бэлтгэл ба Жишиг тест",
    page_icon="🧮",
    layout="wide",
)

TOTAL_QUESTIONS = 40
TOTAL_VARIANTS = 4
EXAM_DURATION_MIN = 100

# ---------------------- STYLES ----------------------
st.markdown(
    """
    <style>
      .main > div {padding-top: 0.5rem;}
      .card {background:#fff;border-radius:16px;padding:16px;box-shadow:0 6px 24px rgba(0,0,0,.06)}
      .pill {display:inline-block;padding:2px 10px;border-radius:999px;background:#eef2ff;color:#3730a3;font-weight:600;font-size:12px;margin-right:6px}
      .timer {font-size:20px;font-weight:700;padding:6px 12px;border-radius:10px;background:#fef3c7;color:#92400e}
      .correct {color:#065f46;font-weight:700}
      .muted {color:#6b7280}
      .qhead {font-weight:700;color:#334155}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------- HELPERS ----------------------
@st.cache_data(show_spinner=False)
def generate_demo_bank(seed: int = 12) -> pd.DataFrame:
    random.seed(seed)
    np.random.seed(seed)
    rows = []
    topics = ["Алгебр", "Функц/График", "Геометр", "Магадлал/Статистик"]
    for v in range(1, TOTAL_VARIANTS + 1):
        for q in range(1, TOTAL_QUESTIONS + 1):
            topic = topics[(q-1) % len(topics)]
            if q % 2 == 1:  # MCQ: шугаман тэгшитгэл / үндсэн логик
                a = random.randint(2, 9)
                b = random.randint(0, 9)
                x = random.randint(1, 9)
                c = a * x + b
                qtext = f"{a}x + {b} = {c}. x-ийн утга?"
                correct_val = x
                options_vals = [correct_val, correct_val+1, correct_val-1, correct_val+2]
                random.shuffle(options_vals)
                correct_letter = "ABCD"[options_vals.index(correct_val)]
                rows.append({
                    "variant": v, "qnum": q, "type": "mcq", "question": qtext,
                    "A": str(options_vals[0]), "B": str(options_vals[1]), "C": str(options_vals[2]), "D": str(options_vals[3]),
                    "correct": correct_letter, "score": 1,
                    "solution": f"{a}x = {c}-{b} ⇒ x={(c-b)//a}",
                    "topic": topic, "difficulty": ["Easy","Medium","Hard"][q%3], "tolerance": ""
                })
            else:  # NUMERIC: тойргийн талбай, илэрхийлэл
                r = random.randint(2, 12)
                area = round(3.1416 * r * r, 2)
                qtext = f"Радиус {r} см тойргийн талбайг π=3.1416 гэж тооцоод олоорой (см²)."
                rows.append({
                    "variant": v, "qnum": q, "type": "num", "question": qtext,
                    "A": "", "B": "", "C": "", "D": "",
                    "correct": area, "score": 1,
                    "solution": f"S=πr²=3.1416×{r}²≈{area}",
                    "topic": topic, "difficulty": ["Easy","Medium","Hard"][q%3], "tolerance": round(0.05*area,2)
                })
    return pd.DataFrame(rows)


def load_bank_from_upload(upload) -> pd.DataFrame | None:
    if upload is None:
        return None
    name = upload.name.lower()
    try:
        if name.endswith('.csv'):
            df = pd.read_csv(upload)
        elif name.endswith('.json'):
            df = pd.read_json(upload)
        else:
            st.error('Зөвхөн CSV/JSON оруулна уу.')
            return None
    except Exception as e:
        st.error(f"Файл уншихад алдаа: {e}")
        return None
    required = ["variant","qnum","type","question","correct","score"]
    miss = [c for c in required if c not in df.columns]
    if miss:
        st.error(f"Дутуу багана: {', '.join(miss)}")
        return None
    df['variant'] = df['variant'].astype(int)
    df['qnum'] = df['qnum'].astype(int)
    return df


def check_numeric_answer(user_text: str, correct_value, tolerance=None) -> bool:
    try:
        if user_text is None or str(user_text).strip() == "":
            return False
        u = float(str(user_text).replace(',', '.'))
        c = float(correct_value)
        tol = float(tolerance) if tolerance not in (None, "", np.nan) else 0.0
        return abs(u - c) <= tol
    except Exception:
        return False


def grade_exam(bank: pd.DataFrame, answers: dict):
    details = []
    total = 0.0
    max_total = 0.0
    for _, row in bank.iterrows():
        key = (int(row.variant), int(row.qnum))
        ans = answers.get(key, None)
        ok = False
        if str(row.type).lower() == 'mcq':
            ok = (str(ans).upper() == str(row.correct).upper())
        else:
            ok = check_numeric_answer(ans, row.correct, row.get('tolerance', None))
        s = float(row.score) if ok else 0.0
        total += s
        max_total += float(row.score)
        details.append({
            'variant': int(row.variant), 'qnum': int(row.qnum), 'type': row.type,
            'topic': row.get('topic',''), 'difficulty': row.get('difficulty',''),
            'correct': row.correct, 'your': ans, 'is_correct': ok, 'score': s, 'max_score': float(row.score)
        })
    return total, max_total, pd.DataFrame(details)


def to_pdf_report(username: str, classroom: str, variant: int, summary: dict, detail_df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=14*mm, bottomMargin=14*mm)
    styles = getSampleStyleSheet()
    elems = []
    elems.append(Paragraph(f"ЭЕШ Математик — Жишиг тест тайлан (Хувилбар {variant})", styles['Title']))
    meta = f"Сурагч: <b>{username or '-'}</b> | Анги: <b>{classroom or '-'}</b> | Огноо: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    elems.append(Paragraph(meta, styles['Normal']))
    elems.append(Spacer(1, 8))
    score_line = f"Нийт оноо: <b>{summary['total']}</b> / {summary['max_total']}  (Зөв: {summary['correct_cnt']}, Буруу: {summary['wrong_cnt']})"
    time_line = f"Зарцуулагдсан: {summary['spent_min']} мин {summary['spent_sec']} сек"
    elems.append(Paragraph(score_line, styles['Heading3']))
    elems.append(Paragraph(time_line, styles['Normal']))
    elems.append(Spacer(1, 6))
    # Topic breakdown
    tb = summary.get('topic_breakdown')
    if isinstance(tb, pd.DataFrame) and not tb.empty:
        data = [["Сэдэв", "Зөв", "Нийт", "Оноо"]] + tb.values.tolist()
        table = Table(data, colWidths=[70*mm, 20*mm, 20*mm, 20*mm])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e2e8f0')),
            ('GRID', (0,0), (-1,-1), 0.25, colors.grey),
            ('ALIGN', (1,1), (-1,-1), 'CENTER'),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ]))
        elems.append(Paragraph("Сэдвийн тайлбар", styles['Heading3']))
        elems.append(table)
        elems.append(Spacer(1, 6))
    # Details
    header = ["#", "Төрөл", "Зөв", "Таны", "Оноо"]
    rows = []
    for _, r in detail_df.sort_values('qnum').iterrows():
        rows.append([int(r.qnum), str(r.type).upper(), str(r.correct), str(r.your), f"{r.score}/{r.max_score}"])
    t2 = Table([header] + rows, colWidths=[12*mm, 18*mm, 28*mm, 28*mm, 20*mm])
    t2.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#f1f5f9')),
        ('GRID', (0,0), (-1,-1), 0.25, colors.grey),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
    ]))
    elems.append(Paragraph("Асуултын дэлгэрэнгүй", styles['Heading3']))
    elems.append(t2)
    doc.build(elems)
    pdf = buf.getvalue()
    buf.close()
    return pdf

# ---------------------- SIDEBAR ----------------------
st.sidebar.header("Тохиргоо")
role = st.sidebar.selectbox("Эрх", ["Сурагч", "Багш/Админ"])
username = st.sidebar.text_input("Нэр/ID", value="student")
classroom = st.sidebar.text_input("Анги/Бүлэг", value="12A")

with st.sidebar.expander("Асуултын сан импорт (CSV/JSON)"):
    up = st.file_uploader("Файл оруулах", type=["csv","json"], help="Дээрх форматтай файлыг оруулна уу.")
    bank_df = load_bank_from_upload(up)
    if bank_df is None:
        st.info("Импорт хийгдээгүй тул демо сан үүсгэлээ (4×40).")
        bank_df = generate_demo_bank()

# ---------------------- SESSION ----------------------
ss = st.session_state
if 'started' not in ss: ss.started = False
if 'submitted' not in ss: ss.submitted = False
if 'start_time' not in ss: ss.start_time = None
if 'answers' not in ss: ss.answers = {}
if 'active_variant' not in ss: ss.active_variant = 1

# ---------------------- PREP GUIDE ----------------------
st.title("🧮 ЭЕШ Математик — Бэлтгэл + Жишиг тест")

with st.expander("ЭЕШ-ийн бэлтгэл амжилттай хийх арга — ", expanded=True):
    st.markdown(
        """
        **1. Сэдэвчилсэн сэдвийн дагуу давтах**: Алгебр → Функц/График → Геометр → Магадлал/Статистик.
        
        **2. Томьёоны дэвтэртэй байх**: Шинэ томьёог томьёоны дэвтэр дээрээ бичих, өдөр бүр 10–15 минут томьёонуудаа эргэж харж байх.
        
        **3. Өгөгдөлтэй ажиллах дадлага**: график тайлбарлах, хүснэгт унших, нэгж хувиргах.
        
        **4. Цагийн менежмент**: 100 минут = 40 бодлого → дунджаар 1 бодлого 2–2.5 мин.
        
        **5. Алдаа-тайлан**: буруу бодсон бодлогуудыг төрөл, шалтгаанаар тэмдэглэж долоо хоног бүр эргэж харах.
        
        **6. Жишиг тест**: Жишиг тестийг 2–3 удаа хийж үзэх (таймертай).
        """
    )

# ---------------------- VARIANT PICKER ----------------------
colA, colB, colC, colD = st.columns([2,1,1,2])
with colA:
    v = st.selectbox("Хувилбар сонгох", sorted(bank_df['variant'].unique().tolist()), index=0)
with colB:
    st.metric("Нийт асуулт", TOTAL_QUESTIONS)
with colC:
    st.metric("Нийт хувилбар", TOTAL_VARIANTS)
with colD:
    pass

variant_df = bank_df[bank_df['variant'] == v].sort_values('qnum').reset_index(drop=True)
if len(variant_df) < TOTAL_QUESTIONS:
    st.warning(f"Хувилбар {v} дээр {len(variant_df)} асуулт байна. {TOTAL_QUESTIONS} байх ёстой.")

# ---------------------- CONTROLS (Start/Save/Submit) ----------------------
ctrl = st.container()
with ctrl:
    c1, c2, c3, c4 = st.columns([1.5,1,1,2])
    with c1:
        if (not ss.started) or ss.active_variant != v:
            if st.button("▶️ Эхлүүлэх/Дахин эхлүүлэх", use_container_width=True):
                ss.started = True
                ss.active_variant = v
                ss.start_time = datetime.now()
                ss.submitted = False
                ss.answers = {}
                st.rerun()
        else:
            st.success(f"Хувилбар {v} идэвхтэй")
    with c2:
        if ss.started and not ss.submitted:
            if st.button("💾 Түр хадгалах", use_container_width=True):
                st.toast("Хадгаллаа (session)")
    with c3:
        if ss.started and not ss.submitted:
            if st.button("🛑 Дуусгах/Илгээх", use_container_width=True):
                ss.submitted = True
                st.rerun()
    with c4:
        if ss.started:
            elapsed = datetime.now() - ss.start_time
            remain = max(timedelta(minutes=EXAM_DURATION_MIN) - elapsed, timedelta(seconds=0))
            mins = int(remain.total_seconds() // 60)
            secs = int(remain.total_seconds() % 60)
            st.markdown(f"<span class='timer'>⏱ Үлдсэн хугацаа: {mins:02d}:{secs:02d}</span>", unsafe_allow_html=True)
            if remain.total_seconds() <= 0 and not ss.submitted:
                st.warning("Хугацаа дууслаа. Автоматаар илгээв.")
                ss.submitted = True
                st.rerun()

# ---------------------- QUESTION RENDER ----------------------

def render_question(row):
    q_key = (int(row.variant), int(row.qnum))
    st.markdown(f"<div class='qhead'>Асуулт #{int(row.qnum)}</div>", unsafe_allow_html=True)
    st.write(row.question)

    disabled = (not ss.started) or ss.submitted or (ss.active_variant != v)
    prev = ss.answers.get(q_key)

    if str(row.type).lower() == 'mcq':
        opt_keys = ["A","B","C","D"]
        # зөвхөн байгаа сонголтуудыг дүүргэнэ
        options = [k for k in opt_keys if k in row.index and pd.notna(row[k])]
        labels = []
        for k in options:
            label = row[k]
            label = "" if (isinstance(label, float) and math.isnan(label)) else str(label)
            labels.append(f"{k}. {label}")
        if not options:
            st.warning("Энэ асуултад сонголт алга")
            st.divider()
            return
        # index-г аюулгүй тооцоолох
        if prev in options:
            sel_index = options.index(prev)
        else:
            sel_index = 0
        choice = st.radio(
            label="Сонголт",
            options=options,
            index=sel_index,
            key=f"q_{q_key}",
            horizontal=True,
            disabled=disabled,
            captions=labels,
        )
        if not disabled:
            ss.answers[q_key] = choice
    else:
        val = st.text_input("Хариу (тоо)", value=str(prev) if prev not in (None, 'None') else "", key=f"q_{q_key}", disabled=disabled)
        if not disabled:
            ss.answers[q_key] = val

    with st.expander("Тайлбар/Шийд (илгээсэний дараа)"):
        if ss.submitted:
            if str(row.type).lower() == 'mcq':
                st.markdown(f"Зөв хариулт: <span class='correct'>{row.correct}</span>", unsafe_allow_html=True)
            else:
                tol = row.get('tolerance', '')
                tol_txt = f" (±{tol})" if tol not in (None, "", np.nan) else ""
                st.markdown(f"Зөв хариулт: <span class='correct'>{row.correct}{tol_txt}</span>", unsafe_allow_html=True)
            st.write(row.get('solution',''))
        else:
            st.markdown("<span class='muted'>Илгээсний дараа харагдана</span>", unsafe_allow_html=True)
    st.divider()

left, right = st.columns([3,1])
with left:
    st.markdown("<div class='card'>", unsafe_allow_html=True)
    if ss.started:
        for _, row in variant_df.iterrows():
            render_question(row)
    else:
        st.info("Хувилбар сонгоод 'Эхлүүлэх' дарна уу.")
    st.markdown("</div>", unsafe_allow_html=True)
with right:
    st.subheader("Явц")
    if ss.started:
        answered = sum(1 for (vk,qk), val in ss.answers.items() if vk==ss.active_variant and (val not in (None, "")))
        st.progress(answered / max(1, len(variant_df)))
        st.write(f"Хариулсан: {answered} / {len(variant_df)})")
        st.caption("Шуурхай навигаци")
        cols = st.columns(5)
        for i, (_, row) in enumerate(variant_df.iterrows()):
            key = (int(row.variant), int(row.qnum))
            filled = (key in ss.answers) and (ss.answers[key] not in (None, ""))
            cols[i%5].button(f"{int(row.qnum)}", type=("primary" if filled else "secondary"))

# ---------------------- RESULTS ----------------------
if ss.submitted and ss.started:
    total, max_total, detail_df = grade_exam(variant_df, ss.answers)
    correct_cnt = int(detail_df['is_correct'].sum())
    wrong_cnt = len(detail_df) - correct_cnt
    percent = 0 if max_total == 0 else round(100*total/max_total,1)

    st.success(f"Дүн: {total} / {max_total}  ({percent}%) • Зөв: {correct_cnt} • Буруу: {wrong_cnt}")

    # Topic breakdown
    if 'topic' in variant_df.columns:
        topic_grp = detail_df.groupby('topic', dropna=False).agg(
            Зөв=("is_correct","sum"), Нийт=("is_correct","count"), Оноо=("score","sum")
        ).reset_index().rename(columns={"topic":"Сэдэв"})
    else:
        topic_grp = pd.DataFrame()

    with st.expander("Дэлгэрэнгүй хүснэгт"):
        st.dataframe(detail_df.drop(columns=['variant']).sort_values('qnum'), use_container_width=True)

    # CSV download
    result_df = detail_df.copy()
    result_df.insert(0, 'username', username)
    result_df.insert(1, 'classroom', classroom)
    result_df.insert(2, 'timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    csv_bytes = result_df.to_csv(index=False).encode('utf-8-sig')
    st.download_button("CSV татах", data=csv_bytes, file_name=f"result_variant{v}.csv", mime="text/csv")

    # PDF download
    summary = {
        'total': total, 'max_total': max_total,
        'correct_cnt': correct_cnt, 'wrong_cnt': wrong_cnt,
        'spent_min': int((datetime.now()-ss.start_time).total_seconds()//60),
        'spent_sec': int((datetime.now()-ss.start_time).total_seconds()%60),
        'topic_breakdown': topic_grp if not topic_grp.empty else pd.DataFrame(),
    }
    pdf_bytes = to_pdf_report(username, classroom, v, summary, detail_df)
    st.download_button("PDF тайлан татах", data=pdf_bytes, file_name=f"report_variant{v}.pdf", mime="application/pdf")

# ---------------------- TEACHER PANEL ----------------------
if role == "Багш/Админ":
    st.divider()
    st.subheader("Багш/Админ самбар")
    st.caption("Импортолсон/демо сангийн эхний мөрүүд")
    st.dataframe(bank_df.head(20), use_container_width=True)
    st.caption("Формат: variant,qnum,type,question,A,B,C,D,correct,score,solution,topic,difficulty,tolerance")
    st.download_button("Жишээ CSV татах (одоогийн сангаас)", data=bank_df.to_csv(index=False).encode('utf-8-sig'), file_name='sample_bank.csv', mime='text/csv')

st.caption("© 2025 • Streamlit")
