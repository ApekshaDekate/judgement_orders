import requests
from bs4 import BeautifulSoup
from fastapi.middleware.cors import CORSMiddleware
from fastapi import APIRouter, Request, Form, Query
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse
# from fastapi.middleware.cors import CORSMiddleware

# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],  # or your frontend domain
#     allow_methods=["*"],
#     allow_headers=["*"],
# )



DELHI_HC_URL = "https://delhihighcourt.nic.in/app"

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/delhi", response_class=HTMLResponse)
def delhi_form(request: Request):
    return templates.TemplateResponse("delhi_form.html", {"request": request})

@router.get("/sitting_judges")
def get_sitting_judges():
    url = f"{DELHI_HC_URL}/sitting-judges-wise-data"
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    options = soup.select("select[name='s_judge_name'] option")
    return [{"id": o.get("value"), "name": o.text.strip()} for o in options if o.get("value")]

@router.get("/case-types")
def get_case_types():
    url = f"{DELHI_HC_URL}/case-number"
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")

    case_types = [{"id": o.get("value"), "name": o.text.strip()}
                  for o in soup.select("select[name='case_type'] option") if o.get("value")]
    years = [{"id": o.get("value"), "name": o.text.strip()}
             for o in soup.select("select[name='year'] option") if o.get("value")]

    return case_types, years

@router.get("/former_judges")
def get_former_judges():
    url = f"{DELHI_HC_URL}/former-judges-wise"
    r = requests.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    options = soup.select("select[name='judge_name'] option")
    return [{"id": o.get("value"), "name": o.text.strip()} for o in options if o.get("value")]

# -----------------------
# Search Endpoints
# -----------------------
def submit_form(url: str, payload_extra: dict):
    """Utility to load form, extract csrf + captcha + randomid, and submit."""
    session = requests.Session()
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": url,
    }

    # Step 1: Load form page
    resp = session.get(url, headers=headers, timeout=30)
    soup = BeautifulSoup(resp.text, "html.parser")
    with open("debug_response.html", "w", encoding="utf-8") as f:
        f.write(soup.prettify())

    payload = {}


    token = soup.find("input", {"name": "_token"})
    if token:
        payload["_token"] = token.get("value", "")

    randomid = soup.find("input", {"name": "randomid"})
    if randomid:
        payload["randomid"] = randomid.get("value", "")

    # Captcha may not always exist
    captcha_val = soup.find("span", {"id": "captcha-code"})
    if captcha_val:
        payload["captchaInput"] = captcha_val.text.strip()

    # Step 2: Build payload
    
    payload.update(payload_extra)

    # Step 3: Submit
    r = session.post(url, headers=headers, data=payload, timeout=30)
    return r.text

def extract_results_table(html: str, save_to: str = "full_response.html") -> str:
    # Save raw HTML to file for debugging
    with open(save_to, "w", encoding="utf-8") as f:
        f.write(html)
    soup = BeautifulSoup(html, "html.parser")
    
    # grab record header + subtext if present
    record_card = soup.find("div", {"class": "record-card"})
    header_html = str(record_card) if record_card else ""

    # grab the result table (works for judge-wise, case no., etc.)
    table = soup.find("table")  # catch any <table> instead of fixed ID
    table_html = str(table) if table else "<p>No results found</p>"

    return header_html + table_html

from datetime import datetime

def format_date(date_str: str) -> str:
    """Convert yyyy-mm-dd to dd-mm-yyyy"""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m-%Y")
    except ValueError:
        return date_str  # fallback

@router.post("/neutral_citation/search")
def search_neutral_citation(neutral_citation: str = Form(...)):
    url = f"{DELHI_HC_URL}/neutral-citation"
    payload = {"neutral_citation": neutral_citation}
    html = submit_form(url, payload)
    table_html = extract_results_table(html)
    return HTMLResponse(content=table_html)


@router.post("/case-number/search")
def search_case(case_type: str = Form(...), case_number: str = Form(...), year: str = Form(...)):
    url = f"{DELHI_HC_URL}/case-number"
    payload = {
        "case_type": case_type,
        "case_number": case_number,
        "year": year,
    }
    html = submit_form(url, payload)
    table_html = extract_results_table(html)
    return HTMLResponse(content=table_html)


@router.post("/search_sitting_judge")
def search_sitting_judge(judge_id: str = Form(...), from_date: str = Form(...), to_date: str = Form(...)):
    url = f"{DELHI_HC_URL}/sitting-judges-wise-data"
    payload = {
        "s_judge_name": judge_id,
        "from_date": format_date(from_date),
        "to_date": format_date(to_date),
    }
    html = submit_form(url, payload)
    table_html = extract_results_table(html)
    return HTMLResponse(content=table_html)


@router.post("/search_former_judge")
def search_former_judge(judge_id: str = Form(...), from_date: str = Form(...), to_date: str = Form(...)):
    url = f"{DELHI_HC_URL}/former-judges-wise"
    payload = {
        "judge_name": judge_id,
        "from_date": format_date(from_date),
        "to_date": format_date(to_date)
    }
    html = submit_form(url, payload)
    table_html = extract_results_table(html)
    return HTMLResponse(content=table_html)


@router.post("/search_by_date")
def search_by_date(from_date: str = Form(...), to_date: str = Form(...)):
    url = f"{DELHI_HC_URL}/judgement-dates-wise"
    payload = {"from_date": format_date(from_date),"to_date": format_date(to_date)}
    html = submit_form(url, payload)
    table_html = extract_results_table(html)
    return HTMLResponse(content=table_html)


@router.post("/search_by_party")
def search_by_party(
    titlesel: str = Form("0"),   # P = Petitioner, R = Respondent, 0 = Don't Know
    party_name: str = Form(""),
    from_date: str = Form(""),
    to_date: str = Form("")
):
    url = f"{DELHI_HC_URL}/party-name-wise-judgement"

    payload = {
        "titlesel": titlesel,
        "party_name": party_name,
        "from_date": format_date(from_date),
        "to_date": format_date(to_date)
    }

    html = submit_form(url, payload)
    table_html = extract_results_table(html)
    return HTMLResponse(content=table_html)
