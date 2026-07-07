import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime, timedelta
from openai import OpenAI
import pypdf
import io
import requests
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

st.set_page_config(
    page_title="국내 주식 대시보드",
    page_icon="📈",
    layout="wide"
)

# ── 상수 ──────────────────────────────────────────────────────────────────
STOCKS = {
    "삼성전자": "005930.KS",
    "SK하이닉스": "000660.KS",
    "LG에너지솔루션": "373220.KS",
    "삼성바이오로직스": "207940.KS",
    "현대차": "005380.KS",
    "셀트리온": "068270.KS",
    "POSCO홀딩스": "005490.KS",
    "카카오": "035720.KS",
    "NAVER": "035420.KS",
    "KB금융": "105560.KS",
}

PERIOD_OPTIONS = {
    "1개월": "1mo",
    "3개월": "3mo",
    "6개월": "6mo",
    "1년": "1y",
    "2년": "2y",
}

DART_REPORT_TYPES = {
    "전체": "",
    "정기공시": "A",
    "주요사항보고": "B",
    "발행공시": "C",
    "지분공시": "D",
    "기타공시": "E",
    "외부감사관련": "F",
    "펀드공시": "G",
    "자산유동화": "H",
    "거래소공시": "I",
    "공정위공시": "J",
}

DART_PERIOD_OPTIONS = {
    "1개월": 30,
    "3개월": 90,
    "6개월": 180,
    "1년": 365,
}

# ── corpcode.csv 로드 ──────────────────────────────────────────────────────
CORPCODE_PATHS = [
    "corpcode.csv",                                                          # 프로젝트 로컬
    r"C:\Users\Hansol\Desktop\AI자동화교육관련\corpcode\corpcode.csv",       # 원본 전체 파일
]

@st.cache_data
def load_corp_codes():
    """프로젝트 corpcode.csv(매핑 테이블) 우선 로드, 없으면 원본 전체 파일에서 stock_code로 검색"""
    import os

    # 1) 프로젝트 매핑 파일 (종목명 → corp_code 직접 매핑)
    local_path = "corpcode.csv"
    if os.path.exists(local_path):
        try:
            df = pd.read_csv(local_path, dtype={"corp_code": str, "stock_code": str})
            if "종목명" in df.columns and "corp_code" in df.columns:
                return dict(zip(df["종목명"], df["corp_code"]))
        except Exception:
            pass

    # 2) 원본 전체 파일에서 stock_code로 검색
    full_path = r"C:\Users\Hansol\Desktop\AI자동화교육관련\corpcode\corpcode.csv"
    if os.path.exists(full_path):
        try:
            df_full = pd.read_csv(full_path, dtype={"corp_code": str, "stock_code": str})
            stock_map = dict(zip(df_full["stock_code"], df_full["corp_code"]))
            # STOCKS 딕셔너리의 ticker에서 stock_code 추출 (예: "005930.KS" → "005930")
            result = {}
            for name, ticker in STOCKS.items():
                sc = ticker.replace(".KS", "").replace(".KQ", "")
                if sc in stock_map:
                    result[name] = stock_map[sc]
            return result
        except Exception:
            pass

    return {}

CORP_CODES = load_corp_codes()

# ── 캐시 함수 ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=300)
def get_stock_data(ticker, period):
    stock = yf.Ticker(ticker)
    hist = stock.history(period=period)
    info = stock.info
    return hist, info

@st.cache_data(ttl=600)
def fetch_dart_disclosures(api_key, corp_code, bgn_de, end_de, pblntf_ty=""):
    """OpenDart 공시목록 API 호출"""
    url = "https://opendart.fss.or.kr/api/list.json"
    params = {
        "crtfc_key": api_key,
        "corp_code": corp_code,
        "bgn_de": bgn_de,
        "end_de": end_de,
        "page_no": 1,
        "page_count": 20,
    }
    if pblntf_ty:
        params["pblntf_ty"] = pblntf_ty
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") == "000":
            return data.get("list", [])
        return []
    except Exception:
        return []

def format_number(value):
    if value is None:
        return "N/A"
    if value >= 1_000_000_000_000:
        return f"{value / 1_000_000_000_000:.2f}조"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.0f}억"
    return f"{value:,.0f}"

# ── 사이드바 ──────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ 설정")
    selected_stocks = st.multiselect(
        "종목 선택",
        options=list(STOCKS.keys()),
        default=list(STOCKS.keys()),
    )
    selected_period = st.selectbox(
        "조회 기간",
        options=list(PERIOD_OPTIONS.keys()),
        index=2,
    )
    chart_type = st.radio(
        "차트 유형",
        options=["캔들스틱", "라인"],
        index=0,
    )

    st.markdown("---")

    # ── OpenDart API 키 + 조회 설정 ──
    st.subheader("🏛️ OpenDart 공시 조회")
    dart_api_key = st.text_input(
        "OpenDart API Key",
        type="password",
        placeholder="발급받은 API 키 입력",
        help="https://opendart.fss.or.kr 에서 발급",
    )
    if dart_api_key:
        st.success("API 키 입력됨", icon="✅")

    dart_stocks = st.multiselect(
        "조회 종목",
        options=list(STOCKS.keys()),
        default=list(STOCKS.keys())[:5],
        key="dart_stocks",
    )
    dart_period = st.selectbox(
        "조회 기간",
        options=list(DART_PERIOD_OPTIONS.keys()),
        index=0,
        key="dart_period",
    )
    dart_type_label = st.selectbox(
        "공시 유형",
        options=list(DART_REPORT_TYPES.keys()),
        index=0,
        key="dart_type",
    )
    fetch_btn = st.button(
        "🔍 공시 조회",
        use_container_width=True,
        type="primary",
        disabled=not dart_api_key,
        help="OpenDart API 키를 먼저 입력하세요." if not dart_api_key else "",
    )

    st.markdown("---")

    # ── PDF 업로드 ──
    st.subheader("📄 PDF 문서 업로드")
    st.markdown("""
    <style>
    [data-testid="stFileUploaderDropzone"] {
        background: linear-gradient(135deg, #f0f4ff 0%, #e8f0fe 100%);
        border: 2px dashed #4a90d9 !important;
        border-radius: 12px !important;
        padding: 1rem !important;
        text-align: center;
        transition: all 0.2s;
    }
    [data-testid="stFileUploaderDropzone"]:hover {
        background: linear-gradient(135deg, #e0ecff 0%, #d0e4ff 100%);
        border-color: #2563eb !important;
    }
    </style>
    """, unsafe_allow_html=True)

    uploaded_pdf = st.file_uploader(
        "PDF 드래그 또는 클릭 업로드",
        type=["pdf"],
        label_visibility="collapsed",
        help="업로드된 PDF 내용이 챗봇 컨텍스트에 포함됩니다.",
    )

    pdf_text = ""
    pdf_info = {}
    if uploaded_pdf is not None:
        try:
            reader = pypdf.PdfReader(io.BytesIO(uploaded_pdf.read()))
            pages = len(reader.pages)
            raw_text = "\n".join(page.extract_text() or "" for page in reader.pages)
            pdf_text = raw_text.strip()
            pdf_info = {"name": uploaded_pdf.name, "pages": pages, "chars": len(pdf_text)}
            st.success(f"✅ **{uploaded_pdf.name}**")
            col_a, col_b = st.columns(2)
            col_a.metric("페이지", f"{pages}p")
            col_b.metric("글자 수", f"{len(pdf_text):,}")
            if not pdf_text:
                st.warning("텍스트 추출 불가 (이미지 PDF)")
        except Exception as e:
            st.error(f"PDF 오류: {e}")

    st.markdown("---")

    # ── OpenAI API 키 ──
    st.subheader("🤖 AI 챗봇 설정")
    openai_api_key = st.text_input(
        "OpenAI API Key",
        type="password",
        placeholder="sk-...",
        help="OpenAI API 키를 입력하세요.",
    )
    if openai_api_key:
        st.success("API 키 입력됨", icon="✅")

    st.markdown("---")

    # ── 이메일 보고서 설정 ──
    st.subheader("📧 이메일 보고서")
    email_sender = st.text_input(
        "발신자 Gmail 계정",
        placeholder="example@gmail.com",
        help="Gmail 계정을 입력하세요.",
        key="email_sender",
    )
    email_app_pw = st.text_input(
        "앱 비밀번호",
        type="password",
        placeholder="Gmail 앱 비밀번호 16자리",
        help="Google 계정 → 보안 → 2단계 인증 → 앱 비밀번호에서 발급",
        key="email_app_pw",
    )
    email_receiver = st.text_input(
        "수신자 이메일",
        placeholder="receiver@example.com",
        key="email_receiver",
    )

    st.markdown("---")
    st.caption("데이터 출처: Yahoo Finance · OpenDart")
    st.caption("5분마다 자동 갱신")

if not selected_stocks:
    st.warning("사이드바에서 종목을 선택해주세요.")
    st.stop()

period = PERIOD_OPTIONS[selected_period]

# ── 종목 현황 카드 ──────────────────────────────────────────────────────────
st.title("📈 국내 주식 대시보드")
st.markdown("---")
st.subheader("📊 종목 현황")
cols = st.columns(min(len(selected_stocks), 5))

summary_data = []
for i, name in enumerate(selected_stocks):
    ticker = STOCKS[name]
    hist, info = get_stock_data(ticker, period)
    if hist.empty:
        continue
    current_price = hist["Close"].iloc[-1]
    prev_price = hist["Close"].iloc[-2] if len(hist) > 1 else current_price
    change = current_price - prev_price
    change_pct = (change / prev_price) * 100
    summary_data.append({
        "종목명": name,
        "현재가": current_price,
        "전일대비": change,
        "등락률(%)": change_pct,
        "거래량": hist["Volume"].iloc[-1],
        "52주 최고": hist["High"].max(),
        "52주 최저": hist["Low"].min(),
    })
    with cols[i % 5]:
        arrow = "▲" if change >= 0 else "▼"
        st.metric(label=name, value=f"{current_price:,.0f}원",
                  delta=f"{arrow} {abs(change_pct):.2f}%")

st.markdown("---")

# ── 주가 차트 ──────────────────────────────────────────────────────────────
st.subheader("📉 주가 차트")
tabs = st.tabs(selected_stocks)

for i, name in enumerate(selected_stocks):
    ticker = STOCKS[name]
    hist, info = get_stock_data(ticker, period)
    if hist.empty:
        with tabs[i]:
            st.warning(f"{name} 데이터를 불러올 수 없습니다.")
        continue

    with tabs[i]:
        col1, col2, col3, col4 = st.columns(4)
        current_price = hist["Close"].iloc[-1]
        prev_price = hist["Close"].iloc[-2] if len(hist) > 1 else current_price
        change = current_price - prev_price
        change_pct = (change / prev_price) * 100

        with col1:
            st.metric("현재가", f"{current_price:,.0f}원", delta=f"{change_pct:+.2f}%")
        with col2:
            st.metric("시가총액", format_number(info.get("marketCap")))
        with col3:
            st.metric("52주 최고가", f"{hist['High'].max():,.0f}원")
        with col4:
            st.metric("52주 최저가", f"{hist['Low'].min():,.0f}원")

        if chart_type == "캔들스틱":
            fig = go.Figure(data=[go.Candlestick(
                x=hist.index, open=hist["Open"], high=hist["High"],
                low=hist["Low"], close=hist["Close"], name=name,
                increasing_line_color="red", decreasing_line_color="blue",
            )])
        else:
            fig = go.Figure(data=[go.Scatter(
                x=hist.index, y=hist["Close"], mode="lines", name=name,
                line=dict(color="royalblue", width=2),
                fill="tozeroy", fillcolor="rgba(65,105,225,0.1)",
            )])

        if len(hist) >= 20:
            fig.add_trace(go.Scatter(
                x=hist.index, y=hist["Close"].rolling(20).mean(),
                mode="lines", name="MA20",
                line=dict(color="orange", width=1.5, dash="dot"),
            ))
        if len(hist) >= 60:
            fig.add_trace(go.Scatter(
                x=hist.index, y=hist["Close"].rolling(60).mean(),
                mode="lines", name="MA60",
                line=dict(color="purple", width=1.5, dash="dash"),
            ))

        fig.update_layout(
            title=f"{name} ({ticker}) 주가 추이",
            xaxis_title="날짜", yaxis_title="주가 (원)",
            height=450, xaxis_rangeslider_visible=False,
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        st.plotly_chart(fig, use_container_width=True)

        vol_colors = ["red" if c >= o else "blue"
                      for c, o in zip(hist["Close"], hist["Open"])]
        fig_vol = go.Figure(data=[
            go.Bar(x=hist.index, y=hist["Volume"], marker_color=vol_colors, name="거래량")
        ])
        fig_vol.update_layout(title="거래량", height=200, showlegend=False)
        st.plotly_chart(fig_vol, use_container_width=True)

st.markdown("---")

# ── 종목 비교 ──────────────────────────────────────────────────────────────
st.subheader("🔀 종목 비교 (수익률 기준)")
if len(selected_stocks) >= 2:
    returns_df = pd.DataFrame()
    for name in selected_stocks:
        hist, _ = get_stock_data(STOCKS[name], period)
        if not hist.empty:
            returns_df[name] = (hist["Close"] / hist["Close"].iloc[0] - 1) * 100
    if not returns_df.empty:
        fig_cmp = px.line(returns_df,
                          title=f"종목별 수익률 비교 ({selected_period})",
                          labels={"value": "수익률 (%)", "variable": "종목"}, height=400)
        fig_cmp.update_layout(hovermode="x unified",
                               legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        fig_cmp.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)
        st.plotly_chart(fig_cmp, use_container_width=True)
else:
    st.info("비교 차트를 보려면 2개 이상의 종목을 선택하세요.")

st.markdown("---")

# ── 종목 요약 테이블 ────────────────────────────────────────────────────────
st.subheader("📋 종목 요약 테이블")
if summary_data:
    df_summary = pd.DataFrame(summary_data)
    df_summary["현재가"]   = df_summary["현재가"].apply(lambda x: f"{x:,.0f}원")
    df_summary["전일대비"] = df_summary["전일대비"].apply(lambda x: f"{x:+,.0f}원")
    df_summary["등락률(%)"] = df_summary["등락률(%)"].apply(lambda x: f"{x:+.2f}%")
    df_summary["거래량"]   = df_summary["거래량"].apply(lambda x: f"{x:,.0f}")
    df_summary["52주 최고"] = df_summary["52주 최고"].apply(lambda x: f"{x:,.0f}원")
    df_summary["52주 최저"] = df_summary["52주 최저"].apply(lambda x: f"{x:,.0f}원")
    st.dataframe(df_summary.set_index("종목명"), use_container_width=True)

st.caption(f"마지막 갱신: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

st.markdown("---")

# ── OpenDart 공시 정보 ──────────────────────────────────────────────────────
st.subheader("🏛️ OpenDart 공시 정보")

if not dart_api_key:
    st.info("사이드바에서 OpenDart API 키와 조회 조건을 설정하고 [🔍 공시 조회] 버튼을 눌러주세요.", icon="🔑")
elif not dart_stocks:
    st.caption("사이드바에서 조회할 종목을 선택하세요.")
elif fetch_btn or st.session_state.get("dart_fetched"):
    st.session_state["dart_fetched"] = True

    end_dt   = datetime.now()
    bgn_dt   = end_dt - timedelta(days=DART_PERIOD_OPTIONS[dart_period])
    bgn_de   = bgn_dt.strftime("%Y%m%d")
    end_de   = end_dt.strftime("%Y%m%d")
    pblntf_ty = DART_REPORT_TYPES[dart_type_label]

    all_disclosures = []
    missing_codes   = []

    progress = st.progress(0, text="공시 데이터 수집 중...")
    for idx, name in enumerate(dart_stocks):
        corp_code = CORP_CODES.get(name)
        if not corp_code:
            missing_codes.append(name)
            progress.progress((idx + 1) / len(dart_stocks))
            continue
        items = fetch_dart_disclosures(dart_api_key, corp_code, bgn_de, end_de, pblntf_ty)
        for item in items:
            all_disclosures.append({
                "종목명": name,
                "접수일자": item.get("rcept_dt", ""),
                "보고서명": item.get("report_nm", ""),
                "제출인": item.get("flr_nm", ""),
                "접수번호": item.get("rcept_no", ""),
                "비고": item.get("rm", ""),
            })
        progress.progress((idx + 1) / len(dart_stocks))
    progress.empty()

    if missing_codes:
        st.warning(f"Corp Code 없음: {', '.join(missing_codes)}")

    if all_disclosures:
        df_dart = pd.DataFrame(all_disclosures)
        df_dart["접수일자"] = pd.to_datetime(df_dart["접수일자"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
        df_dart = df_dart.sort_values("접수일자", ascending=False).reset_index(drop=True)

        df_dart["DART 링크"] = df_dart["접수번호"].apply(
            lambda r: f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={r}"
        )

        st.markdown(
            f"**{len(df_dart)}건** 조회됨 "
            f"({bgn_dt.strftime('%Y.%m.%d')} ~ {end_dt.strftime('%Y.%m.%d')})"
        )

        stock_tabs = st.tabs(
            [f"{n} ({len(df_dart[df_dart['종목명']==n])}건)" for n in dart_stocks
             if n in df_dart["종목명"].values]
        )
        visible_stocks = [n for n in dart_stocks if n in df_dart["종목명"].values]

        for tab, sname in zip(stock_tabs, visible_stocks):
            with tab:
                sub = df_dart[df_dart["종목명"] == sname][
                    ["접수일자", "보고서명", "제출인", "비고", "DART 링크"]
                ].reset_index(drop=True)

                sub["보고서"] = sub.apply(
                    lambda r: f'<a href="{r["DART 링크"]}" target="_blank">{r["보고서명"]}</a>', axis=1
                )
                display_cols = ["접수일자", "보고서", "제출인", "비고"]
                st.markdown(
                    sub[display_cols].to_html(escape=False, index=False),
                    unsafe_allow_html=True,
                )

        st.download_button(
            label="⬇️ 전체 공시 CSV 다운로드",
            data=df_dart.drop(columns=["DART 링크"]).to_csv(index=False, encoding="utf-8-sig"),
            file_name=f"dart_disclosure_{end_de}.csv",
            mime="text/csv",
        )
    else:
        st.info(f"해당 기간({dart_period}) 동안 조회된 공시가 없습니다.")

st.markdown("---")

# ── AI 챗봇 ────────────────────────────────────────────────────────────────
st.subheader("🤖 AI 주식 분석 챗봇")
st.caption("수집된 주식 데이터 · 공시 정보 · PDF를 기반으로 GPT-4o-mini가 답변합니다.")

if not openai_api_key:
    st.info("사이드바에서 OpenAI API 키를 입력하면 챗봇을 사용할 수 있습니다.", icon="🔑")
else:
    def build_stock_context():
        lines = [
            f"오늘 날짜: {datetime.now().strftime('%Y년 %m월 %d일')}",
            f"조회 기간: {selected_period}",
            "",
            "=== 현재 국내 주식 10종목 데이터 ===",
        ]
        for row in summary_data:
            name = row["종목명"]
            ticker = STOCKS[name]
            hist, info = get_stock_data(ticker, period)
            recent = hist["Close"].tail(5).round(0).tolist()
            recent_str = " → ".join(f"{p:,.0f}" for p in recent)
            lines.append(
                f"\n[{name} / {ticker}]\n"
                f"  현재가: {row['현재가']:,.0f}원 | 전일대비: {row['전일대비']:+,.0f}원 ({row['등락률(%)']:+.2f}%)\n"
                f"  시가총액: {format_number(info.get('marketCap'))} | 거래량: {row['거래량']:,.0f}\n"
                f"  52주 최고: {row['52주 최고']:,.0f}원 | 52주 최저: {row['52주 최저']:,.0f}원\n"
                f"  최근 5거래일 종가: {recent_str}원"
            )
        return "\n".join(lines)

    # 공시 컨텍스트
    dart_context = ""
    if st.session_state.get("dart_fetched") and dart_api_key:
        dart_rows = []
        for name in selected_stocks[:5]:
            corp_code = CORP_CODES.get(name)
            if not corp_code:
                continue
            end_dt = datetime.now()
            bgn_dt = end_dt - timedelta(days=30)
            items  = fetch_dart_disclosures(dart_api_key, corp_code,
                                            bgn_dt.strftime("%Y%m%d"),
                                            end_dt.strftime("%Y%m%d"))
            for it in items[:3]:
                dart_rows.append(
                    f"[{name}] {it.get('rcept_dt','')} · {it.get('report_nm','')} (제출:{it.get('flr_nm','')})"
                )
        if dart_rows:
            dart_context = "\n=== 최근 공시 정보 (최근 1개월, 종목당 3건) ===\n" + "\n".join(dart_rows)

    # PDF 컨텍스트
    MAX_PDF_CHARS = 6000
    pdf_context_section = ""
    if pdf_text:
        truncated = pdf_text[:MAX_PDF_CHARS]
        note = (f"\n(※ PDF 앞 {MAX_PDF_CHARS:,}자만 포함)" if len(pdf_text) > MAX_PDF_CHARS else "")
        pdf_context_section = (
            f"\n\n=== 업로드된 PDF: {pdf_info.get('name','')} "
            f"({pdf_info.get('pages',0)}p) ===\n{truncated}{note}"
        )

    sources = ["실시간 주식 데이터"]
    if dart_context:
        sources.append("공시 정보")
    if pdf_text:
        sources.append("PDF 문서")

    SYSTEM_PROMPT = f"""당신은 국내 주식 전문 AI 애널리스트입니다.
아래의 {' · '.join(sources)}를 기반으로 사용자 질문에 한국어로 답변하세요.

{build_stock_context()}
{dart_context}
{pdf_context_section}

답변 원칙:
- 데이터에 근거한 객관적인 분석을 제공하세요.
- 투자 권유가 아닌 정보 제공 목적임을 명심하세요.
- 공시 정보가 있으면 관련 주가 영향을 함께 분석하세요.
- PDF 내용이 있으면 주식 데이터와 연관 지어 답변하세요.
- 데이터에 없는 정보는 솔직하게 알려주세요.
"""

    welcome_lines = [f"안녕하세요! 국내 주식 AI 분석 챗봇입니다. 📊\n\n**{len(summary_data)}개 종목** 실시간 데이터를 보유하고 있습니다."]
    if dart_context:
        welcome_lines.append("🏛️ 공시 정보가 로드되어 있습니다.")
    if pdf_text:
        welcome_lines.append(f"📄 PDF 로드됨: `{pdf_info.get('name','')}` ({pdf_info.get('pages',0)}p)")
    welcome_lines.append(
        "\n**예시 질문:**\n"
        "- 오늘 가장 많이 오른 종목은?\n"
        "- 삼성전자 최근 공시 내용 분석해줘\n"
        "- 반도체 관련 종목들 비교해줘\n"
        "- 52주 최저가에 가장 가까운 종목은?"
    )
    init_msg = "\n".join(welcome_lines)

    prev_pdf = st.session_state.get("_pdf_name", "")
    curr_pdf = pdf_info.get("name", "")
    if prev_pdf != curr_pdf:
        st.session_state.chat_messages = [{"role": "assistant", "content": init_msg}]
        st.session_state["_pdf_name"] = curr_pdf

    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = [{"role": "assistant", "content": init_msg}]

    chat_container = st.container(height=500)
    with chat_container:
        for msg in st.session_state.chat_messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    if user_input := st.chat_input("주식·공시·PDF에 대해 질문하세요..."):
        st.session_state.chat_messages.append({"role": "user", "content": user_input})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(user_input)

        with chat_container:
            with st.chat_message("assistant"):
                try:
                    client = OpenAI(api_key=openai_api_key)
                    msgs   = [{"role": "system", "content": SYSTEM_PROMPT}]
                    msgs  += st.session_state.chat_messages[-10:]
                    stream = client.chat.completions.create(
                        model="gpt-4o-mini", messages=msgs,
                        stream=True, temperature=0.7, max_tokens=1000,
                    )
                    response_text = st.write_stream(
                        chunk.choices[0].delta.content or ""
                        for chunk in stream
                        if chunk.choices[0].delta.content
                    )
                    st.session_state.chat_messages.append(
                        {"role": "assistant", "content": response_text}
                    )
                except Exception as e:
                    err = f"오류: {e}"
                    st.error(err)
                    st.session_state.chat_messages.append({"role": "assistant", "content": err})

    if len(st.session_state.chat_messages) > 1:
        if st.button("🗑️ 대화 초기화", key="clear_chat"):
            st.session_state.chat_messages = st.session_state.chat_messages[:1]
            st.rerun()

st.markdown("---")

# ── 이메일 보고서 ────────────────────────────────────────────────────────────
st.subheader("📧 주식 분석 보고서 이메일 발송")
st.caption("수집된 주식 데이터와 최신 뉴스를 바탕으로 AI 보고서를 생성하여 이메일로 발송합니다.")

email_sender   = st.session_state.get("email_sender", "")
email_app_pw   = st.session_state.get("email_app_pw", "")
email_receiver = st.session_state.get("email_receiver", "")

if not openai_api_key:
    st.info("AI 보고서 생성을 위해 사이드바에서 OpenAI API 키를 입력하세요.", icon="🔑")
elif not (email_sender and email_app_pw and email_receiver):
    st.info("사이드바에서 발신자 Gmail 계정, 앱 비밀번호, 수신자 이메일을 입력하세요.", icon="📬")
else:
    # 보고서 대상 종목 선택
    report_stocks = st.multiselect(
        "보고서에 포함할 종목",
        options=selected_stocks,
        default=selected_stocks[:5],
        key="report_stocks",
    )

    # 뉴스 수집 함수
    @st.cache_data(ttl=1800)
    def fetch_stock_news(stock_name, ticker):
        """yfinance 뉴스 수집"""
        try:
            t = yf.Ticker(ticker)
            news = t.news or []
            results = []
            for item in news[:5]:
                content = item.get("content", {})
                title   = content.get("title", "") or item.get("title", "")
                summary = content.get("summary", "") or ""
                pub_raw = content.get("pubDate", "") or item.get("providerPublishTime", "")
                if isinstance(pub_raw, int):
                    pub = datetime.fromtimestamp(pub_raw).strftime("%Y-%m-%d")
                elif isinstance(pub_raw, str) and pub_raw:
                    pub = pub_raw[:10]
                else:
                    pub = ""
                if title:
                    results.append({"title": title, "summary": summary, "date": pub})
            return results
        except Exception:
            return []

    def build_report_context(stocks_to_report):
        """보고서용 데이터 컨텍스트 생성"""
        lines = [
            f"보고서 생성일시: {datetime.now().strftime('%Y년 %m월 %d일 %H:%M')}",
            f"분석 기간: {selected_period}",
            "",
        ]
        for name in stocks_to_report:
            ticker = STOCKS[name]
            hist, info = get_stock_data(ticker, period)
            if hist.empty:
                continue
            cp   = hist["Close"].iloc[-1]
            pp   = hist["Close"].iloc[-2] if len(hist) > 1 else cp
            chg  = cp - pp
            pct  = (chg / pp) * 100
            high = hist["High"].max()
            low  = hist["Low"].min()
            vol  = hist["Volume"].iloc[-1]
            recent = hist["Close"].tail(5).round(0).tolist()

            news_items = fetch_stock_news(name, ticker)
            news_text = ""
            if news_items:
                news_lines = [f"  - [{n['date']}] {n['title']}" + (f"\n    {n['summary'][:120]}" if n['summary'] else "") for n in news_items]
                news_text = "\n최근 뉴스:\n" + "\n".join(news_lines)

            lines.append(
                f"\n■ {name} ({ticker})\n"
                f"  현재가: {cp:,.0f}원  전일대비: {chg:+,.0f}원 ({pct:+.2f}%)\n"
                f"  시가총액: {format_number(info.get('marketCap'))}  거래량: {vol:,.0f}\n"
                f"  52주 최고: {high:,.0f}원  52주 최저: {low:,.0f}원\n"
                f"  최근 5일 종가: {' → '.join(f'{p:,.0f}' for p in recent)}원"
                f"{news_text}"
            )
        return "\n".join(lines)

    def generate_report_html(openai_key, context_text):
        """GPT로 HTML 보고서 생성"""
        client = OpenAI(api_key=openai_key)
        prompt = f"""아래 국내 주식 데이터와 뉴스를 바탕으로 전문적인 주식 분석 보고서를 작성하세요.

{context_text}

요구사항:
1. 보고서는 HTML 형식으로 작성하세요 (이메일용, 인라인 스타일 사용).
2. 구성: 요약 → 종목별 분석 → 주요 뉴스 → 투자 유의사항
3. 종목별 분석에서 주가 추이, 등락 원인(뉴스 기반 추정), 단기 주목 포인트를 포함하세요.
4. 전문적이고 읽기 쉬운 스타일로 작성하세요.
5. 이메일 본문에 적합한 완성된 HTML을 반환하세요 (<!DOCTYPE> 제외, <body> 태그부터 시작).
6. 색상은 파란색 계열(#1a56db) 헤더, 초록색 상승, 빨간색 하락 표시.
7. 투자 권유가 아닌 정보 제공 목적임을 명시하세요.
"""
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=3000,
        )
        return resp.choices[0].message.content

    def send_email_report(sender, app_pw, receiver, subject, html_body):
        """Gmail SMTP로 HTML 이메일 발송"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = sender
        msg["To"]      = receiver
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender, app_pw)
            server.sendmail(sender, receiver, msg.as_string())

    col_gen, col_send = st.columns([1, 1])

    with col_gen:
        gen_btn = st.button(
            "📝 보고서 생성",
            use_container_width=True,
            disabled=not report_stocks,
            help="선택한 종목의 AI 분석 보고서를 생성합니다.",
        )

    with col_send:
        send_btn = st.button(
            "📤 보고서 발송",
            use_container_width=True,
            type="primary",
            disabled=not (report_stocks and st.session_state.get("report_html")),
            help="생성된 보고서를 이메일로 발송합니다.",
        )

    if gen_btn and report_stocks:
        with st.spinner("📊 데이터 수집 및 AI 보고서 생성 중..."):
            try:
                ctx = build_report_context(report_stocks)
                html = generate_report_html(openai_api_key, ctx)
                # <body> 태그 안쪽만 추출
                if "<body" in html:
                    html = html[html.find("<body"):]
                    html = html[html.find(">") + 1:]
                if "</body>" in html:
                    html = html[:html.rfind("</body>")]
                st.session_state["report_html"] = html
                st.session_state["report_stocks"] = report_stocks
                st.success("✅ 보고서가 생성되었습니다. [📤 보고서 발송] 버튼으로 전송하세요.")
            except Exception as e:
                st.error(f"보고서 생성 실패: {e}")

    if st.session_state.get("report_html"):
        st.markdown("#### 📄 생성된 보고서 미리보기")
        with st.expander("보고서 내용 보기", expanded=True):
            st.components.v1.html(
                f"<div style='font-family:sans-serif;font-size:14px'>{st.session_state['report_html']}</div>",
                height=600,
                scrolling=True,
            )

    if send_btn and st.session_state.get("report_html"):
        subject = f"[주식 분석 보고서] {datetime.now().strftime('%Y.%m.%d')} — {', '.join(st.session_state.get('report_stocks', [])[:3])} 등"
        with st.spinner("📧 이메일 발송 중..."):
            try:
                send_email_report(
                    email_sender, email_app_pw, email_receiver,
                    subject, st.session_state["report_html"]
                )
                st.success(f"✅ 보고서가 **{email_receiver}** 로 발송되었습니다!")
                st.balloons()
            except smtplib.SMTPAuthenticationError:
                st.error("❌ 인증 실패: Gmail 앱 비밀번호를 확인하세요. (일반 비밀번호가 아닌 앱 비밀번호 필요)")
            except smtplib.SMTPException as e:
                st.error(f"❌ 이메일 발송 실패: {e}")
            except Exception as e:
                st.error(f"❌ 오류: {e}")
