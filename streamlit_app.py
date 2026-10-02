"""TalentLens — Streamlit recruitment dashboard (Milestone 4).

    streamlit run streamlit_app.py          # http://localhost:8501

Unlike the brief's reference, which hardcodes three candidate dicts, every number here
comes from `app.pipeline.run_pipeline`: the resumes in `sample_resumes/` (or ones you
upload) are parsed, matched, interviewed and pushed through the ATS before the page
renders. The pipeline is cached, so it runs once per set of files, not once per click.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.evaluation import EVAL_JOBS
from app.interview import STAGES, generate_interview_kit
from app.matching import match, skill_gap_report
from app.pipeline import run_pipeline
from app.satisfaction import CSAT_QUESTION, SUS_ITEMS, summarise
from app.voice import VoiceScreen

ROOT = Path(__file__).parent
SIGNAL, OCHRE, CLAY, INK = "#1B5B4C", "#A9701A", "#98363F", "#16211F"
BAND_COLOUR = {"strong": SIGNAL, "possible": OCHRE, "weak": CLAY}
BAND_LABEL = {"strong": "Shortlist", "possible": "Screen", "weak": "Hold"}

st.set_page_config(page_title="TalentLens — Recruitment Copilot", page_icon="🔎", layout="wide")


# --------------------------------------------------------------------------- #
# data — cached so the pipeline runs once per resume set
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner="Parsing, matching and interviewing the cohort…")
def load_pipeline(folder: str, signature: str):
    """`signature` changes when the file set changes, which invalidates the cache."""
    return run_pipeline(folder)


def folder_signature(folder: Path) -> str:
    return "|".join(f"{p.name}:{p.stat().st_size}" for p in sorted(folder.iterdir()) if p.is_file())


with st.sidebar:
    st.markdown("### TalentLens")
    st.caption("AI Recruitment Copilot · Infosys Springboard 7.0 · Batch 3")
    uploads = st.file_uploader("Add resumes (PDF, DOCX, TXT)", type=["pdf", "docx", "txt"],
                               accept_multiple_files=True)
    blind = st.toggle("Bias shield", help="Hide names in every table to screen on skills first")
    st.divider()
    st.caption("Milestone gates")

if uploads:
    work = Path(tempfile.mkdtemp(prefix="talentlens_"))
    for sample in (ROOT / "sample_resumes").iterdir():
        (work / sample.name).write_bytes(sample.read_bytes())
    for f in uploads:
        (work / f.name).write_bytes(f.getbuffer())
    folder = work
else:
    folder = ROOT / "sample_resumes"

result = load_pipeline(str(folder), folder_signature(folder))
candidates = result["candidates"]
ats = result["ats"]
kpis = result["dashboard"]["kpis"]


def shown(name: str, i: int) -> str:
    return f"Candidate {i + 1:02d}" if blind else name


with st.sidebar:
    for label, ok, value in (
        ("M1 · field recall ≥95%", result["milestone_1"]["gate_met"], f"{result['milestone_1']['field_recall']}%"),
        ("M2 · matching accuracy ≥85%", result["milestone_2"]["gate_met"], f"{result['milestone_2']['accuracy']}%"),
        ("M3 · interviews + ATS", result["milestone_3"]["interviews"] > 0, f"{result['milestone_3']['interviews']} run"),
        ("M4 · end-to-end workflow", result["complete"], f"{result['total_ms']} ms"),
    ):
        st.markdown(f"{'✅' if ok else '⬜'} **{label}** — {value}")

# --------------------------------------------------------------------------- #
# header + KPIs
# --------------------------------------------------------------------------- #
st.title("Recruitment dashboard")
st.caption("Every figure below is computed from the resumes in the pipeline. "
           "A dash means the data does not exist yet — nothing is a placeholder.")


def fmt(v, suffix=""):
    return "—" if v is None else f"{v}{suffix}"


k = st.columns(5)
k[0].metric("Candidates in pipeline", fmt(kpis["total_candidates"]))
k[1].metric("Interviews held / scheduled", fmt(kpis["interviews_scheduled"]))
k[2].metric("Shortlist rate", fmt(kpis["shortlist_rate"], "%"))
k[3].metric("Avg. best-role match", fmt(kpis["average_best_match"], "%"))
k[4].metric("Avg. interview score*", fmt(kpis["average_interview_score"], "/100"))
st.caption("*From simulated interviews using a fixed sample answer set. "
           "Offer rate and time-to-hire stay blank until real offers are recorded.")

tab_overview, tab_rank, tab_gap, tab_int, tab_voice, tab_csat, tab_ops = st.tabs(
    ["Overview", "Candidate ranking", "Skill gaps", "Interviews & ATS",
     "Voice screening", "Satisfaction", "System"])

# --------------------------------------------------------------------------- #
# overview
# --------------------------------------------------------------------------- #
with tab_overview:
    left, right = st.columns([3, 2])
    pipe = result["dashboard"]["pipeline"]
    stage_df = pd.DataFrame({"stage": [s.replace("_", " ").title() for s in pipe],
                             "candidates": list(pipe.values())})
    left.plotly_chart(
        go.Figure(go.Funnel(y=stage_df["stage"], x=stage_df["candidates"],
                            marker={"color": SIGNAL}, textinfo="value+percent initial"))
        .update_layout(title="Recruitment pipeline", height=380, margin=dict(l=10, r=10, t=50, b=10)),
        use_container_width=True)
    bands = result["dashboard"]["band_counts"]
    right.plotly_chart(
        px.pie(names=[BAND_LABEL[b] for b in bands], values=list(bands.values()), hole=0.55,
               color=[BAND_LABEL[b] for b in bands],
               color_discrete_map={BAND_LABEL[b]: BAND_COLOUR[b] for b in bands},
               title="All candidate × role decisions")
        .update_layout(height=380, margin=dict(l=10, r=10, t=50, b=10)),
        use_container_width=True)
    st.caption(f"{result['dashboard']['rejected']} candidate(s) were rejected at the mandatory-skill "
               f"gate before screening.")

# --------------------------------------------------------------------------- #
# ranking
# --------------------------------------------------------------------------- #
with tab_rank:
    job = st.selectbox("Role", EVAL_JOBS, format_func=lambda j: j["title"], key="rank_job")
    rows = []
    for i, c in enumerate(candidates):
        m = match(c, job)
        b = m["score_breakdown"]
        rows.append({"Candidate": shown(c["name"], i), "Hiring score": m["hiring_score"],
                     "Decision": BAND_LABEL[m["band"]],
                     "Mandatory coverage %": b["skills"]["must_have_coverage"],
                     "Skills pts": b["skills"]["contribution"],
                     "Experience pts": b["experience"]["contribution"],
                     "Education pts": b["education"]["contribution"],
                     "Missing mandatory": ", ".join(m["missing_must_have"]) or "—"})
    df = pd.DataFrame(rows).sort_values("Hiring score", ascending=False)
    st.dataframe(df, use_container_width=True, hide_index=True,
                 column_config={"Hiring score": st.column_config.ProgressColumn(
                     min_value=0, max_value=100, format="%.1f")})
    stack = df.melt(id_vars="Candidate", value_vars=["Skills pts", "Experience pts", "Education pts"],
                    var_name="Component", value_name="Points")
    st.plotly_chart(px.bar(stack, x="Points", y="Candidate", color="Component", orientation="h",
                           title="Where each score comes from",
                           color_discrete_sequence=[SIGNAL, OCHRE, "#4B4177"])
                    .update_layout(height=360, yaxis={"categoryorder": "total ascending"}),
                    use_container_width=True)

# --------------------------------------------------------------------------- #
# skill gaps
# --------------------------------------------------------------------------- #
with tab_gap:
    job = st.selectbox("Role", EVAL_JOBS, format_func=lambda j: j["title"], key="gap_job")
    reports = [skill_gap_report(c, job) for c in candidates]
    missing: dict = {}
    for r in reports:
        for s in r["missing_must_have"]:
            missing[s] = missing.get(s, 0) + 1
    if missing:
        gdf = pd.DataFrame(sorted(missing.items(), key=lambda kv: -kv[1]),
                           columns=["Skill", "Candidates missing it"])
        st.plotly_chart(px.bar(gdf, x="Candidates missing it", y="Skill", orientation="h",
                               title=f"Most common mandatory gaps — {job['title']}",
                               color_discrete_sequence=[CLAY])
                        .update_layout(height=340, yaxis={"categoryorder": "total ascending"}),
                        use_container_width=True)
    pick = st.selectbox("Candidate report", range(len(candidates)),
                        format_func=lambda i: shown(candidates[i]["name"], i))
    rep = reports[pick]
    st.info(rep["summary"])
    a, b = st.columns(2)
    a.markdown("**Matched** — " + (", ".join(rep["matched_skills"]) or "none"))
    b.markdown("**Missing (mandatory)** — " + (", ".join(rep["missing_must_have"]) or "none"))
    if rep["recommendations"]:
        st.dataframe(pd.DataFrame(rep["recommendations"]), use_container_width=True, hide_index=True)

# --------------------------------------------------------------------------- #
# interviews & ATS
# --------------------------------------------------------------------------- #
with tab_int:
    job = st.selectbox("Role", EVAL_JOBS, format_func=lambda j: j["title"], key="int_job")
    pick = st.selectbox("Candidate", range(len(candidates)),
                        format_func=lambda i: shown(candidates[i]["name"], i), key="int_cand")
    kit = generate_interview_kit(candidates[pick], job)
    st.caption(f"{len(kit['questions'])} questions · difficulty {kit['difficulty']} · "
               f"~{kit['estimated_minutes']} min · mix {kit['mix']}")
    for n, q in enumerate(kit["questions"], 1):
        with st.expander(f"{n}. [{q['category']}] {q['question_text']}"):
            st.markdown(f"**Why this question:** {q['rationale']}")
            st.markdown("**A good answer covers:** " + " · ".join(q["expected_points"]))
    st.subheader("ATS pipeline")
    ats_df = pd.DataFrame(ats.list_candidates())
    if not ats_df.empty:
        if blind:
            ats_df["name"] = [f"Candidate {i + 1:02d}" for i in range(len(ats_df))]
        st.dataframe(ats_df[[c for c in ("name", "job_title", "stage", "interview_score", "external_ref")
                             if c in ats_df.columns]], use_container_width=True, hide_index=True)
    f = ats.funnel()
    c1, c2, c3 = st.columns(3)
    c1.metric("Screened of applied", fmt(f["conversion"].get("screened_of_applied"), "%"))
    c2.metric("Interviewed of applied", fmt(f["conversion"].get("interviewed_of_applied"), "%"))
    c3.metric("ATS events logged", f["events_logged"])

# --------------------------------------------------------------------------- #
# voice screening
# --------------------------------------------------------------------------- #
with tab_voice:
    st.markdown("Record a spoken answer, or paste a transcript. Scoring is 80% content (the same "
                "rubric as the interview module) and 20% delivery, deliberately weighted so "
                "accent and nerves cannot dominate the result.")
    job = st.selectbox("Role", EVAL_JOBS, format_func=lambda j: j["title"], key="voice_job")
    pick = st.selectbox("Candidate", range(len(candidates)),
                        format_func=lambda i: shown(candidates[i]["name"], i), key="voice_cand")
    screen = VoiceScreen(candidates[pick], job)
    prompts = screen.prompts()
    qn = st.radio("Question", range(len(prompts)), format_func=lambda i: prompts[i]["question_text"])
    transcript = ""
    audio = st.audio_input("Record answer") if hasattr(st, "audio_input") else None
    if audio is not None:
        try:
            import speech_recognition as sr
            rec = sr.Recognizer()
            with sr.AudioFile(audio) as src:
                transcript = rec.recognize_google(rec.record(src))
            st.success(f"Transcribed: {transcript}")
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Could not transcribe ({type(exc).__name__}). Paste the transcript below.")
    transcript = st.text_area("Transcript", value=transcript, height=120)
    seconds = st.number_input("Answer length (seconds, optional)", min_value=0, value=0)
    if st.button("Assess answer", type="primary") and transcript.strip():
        out = screen.record(prompts[qn], transcript, seconds or None)
        m1, m2, m3 = st.columns(3)
        m1.metric("Overall", f"{out['score']}/100", out["verdict"])
        m2.metric("Content", f"{out['content_score']}/100")
        m3.metric("Delivery", f"{out['delivery_score']}/100")
        st.write(out["feedback"])
        st.json(out["signals"])

# --------------------------------------------------------------------------- #
# satisfaction
# --------------------------------------------------------------------------- #
with tab_csat:
    st.markdown("The Milestone 4 target is **≥85% satisfaction**. That number has to come from "
                "real users, so it is collected here rather than asserted.")
    if "survey" not in st.session_state:
        st.session_state.survey = []
    with st.form("sus"):
        role = st.selectbox("Your role", ["Recruiter", "Hiring manager", "Candidate", "Mentor / reviewer"])
        csat = st.slider(CSAT_QUESTION, 1, 5, 4)
        ratings = [st.slider(q, 1, 5, 3, key=f"sus{i}") for i, q in enumerate(SUS_ITEMS)]
        if st.form_submit_button("Submit feedback"):
            st.session_state.survey.append({"role": role, "csat": csat, "sus": ratings})
            st.success("Thanks — recorded.")
    s = summarise(st.session_state.survey)
    a, b, c = st.columns(3)
    a.metric("CSAT (4–5 of 5)", fmt(s["csat_percent"], "%"))
    b.metric("SUS", fmt(s["sus_mean"]), s["sus_grade"] or "")
    c.metric("Responses", s["responses"], f"need {s['min_responses']}")
    st.caption(f"Status: {s['status']}")

# --------------------------------------------------------------------------- #
# system
# --------------------------------------------------------------------------- #
with tab_ops:
    st.subheader("End-to-end run")
    st.dataframe(pd.DataFrame(result["stages"]), use_container_width=True, hide_index=True)
    st.caption(f"Workflow complete: {result['complete']} · total {result['total_ms']} ms · "
               f"stages: {' → '.join(STAGES[:6])}")
    st.json({"milestone_1": result["milestone_1"], "milestone_2": result["milestone_2"],
             "milestone_3": {k: v for k, v in result["milestone_3"].items() if k != "funnel"}})
