import os
import re
import requests
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from bs4 import BeautifulSoup
from fastapi import APIRouter, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import httpx

SERVER_URL = "http://128.127.50.120:8000"


router = APIRouter()
templates = Jinja2Templates(directory="templates")

PDFS_ROOT = Path("/media/ibmarray2_1/airstorage/commpdf/")
PDFS_ROOT.mkdir(exist_ok=True)
PDF_DIR = PDFS_ROOT / "bombay"
PDF_DIR.mkdir(exist_ok=True)

def convert_date_format(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d-%m- %Y")


def download_pdf(pdf_url, pdf_path):
    try:
        if not os.path.exists(pdf_path):
            r = requests.get(pdf_url, timeout=10)
            with open(pdf_path, 'wb') as f:
                f.write(r.content)
    except Exception as e:
        print(f"Failed to download PDF: {pdf_url}", e)


def sanitize_filename(name: str) -> str:
    return re.sub(r'[\/*?:"<>| ]+', '_', name.strip())[:80]


def get_bombay_data(
    save_folder: str,
    from_date: str = "",
    to_date: str = "",
    coram: Optional[str] = None,
    actcode: Optional[str] = None,
    actside: Optional[str] = None,
    m_bench: Optional[str] = None,
    m_skey: Optional[str] = None,
    m_sideflg: Optional[str] = None,
    joflag: Optional[str] = None,
    bflag: Optional[str] = None,
    m_no: Optional[str] = None,
    m_yr: Optional[str] = None,
    party: Optional[str] = None,
    ncitation1: Optional[str] = None,
    ncitation2: Optional[str] = None,
    ncitation4: Optional[str] = None,
):
    DATA_URL = "https://bombayhighcourt.nic.in/ord_qry.php"

    data = {
        "CSRFName": "",
        "CSRFToken": "",
        "pageno": "1",
        "m_bench": m_bench or "",
        "m_sideflg": m_sideflg or "",
        "m_skey": m_skey or "",
        "m_no": m_no or "",
        "m_yr": m_yr or "",
        "cino": "",
        "ncitation1": ncitation1 or "",
        "ncitation2": ncitation2 or "",
        "ncitation4": ncitation4 or "",
        "coram": coram or "",
        "actcode": actcode or "",
        "actside": actside or "",
        "party": party or "",
        "advocate": "",
        "m_frmdate": from_date,
        "m_todate": to_date,
        "captcha_code": "1",
        "submit1": "Submit",
    }

    # 🚀 Add joflag ONLY IF SELECTED
    if joflag:
        for j in joflag:
            data.setdefault("joflag[]", []).append(j)

    if bflag:
        for b in bflag:
            data.setdefault("bflag[]", []).append(b)


    print("\n🚀 FINAL PAYLOAD SENT:")
    for k, v in data.items():
        print(f"{k}: {v}")

    response = requests.post(DATA_URL, data=data)
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("table", {"id": "myTable"})
    rows = []

    os.makedirs(save_folder, exist_ok=True)

    if table:
        for row in table.find_all("tr"):
            cols = []
            for cell in row.find_all(["td", "th"]):
                for br in cell.find_all("br"):
                    br.replace_with("\n")

                parts = []

                texts = [t.strip() for t in cell.find_all(string=True) if t.strip()]
                if texts:
                    parts.extend(texts)

                for a_tag in cell.find_all("a", href=True):
                    href = a_tag["href"].strip()
                    link_text = a_tag.get_text(strip=True)

                    filename = sanitize_filename(link_text) + ".pdf"
                    pdf_path = os.path.join(save_folder, filename)
                    full_url = f"https://bombayhighcourt.nic.in/{href.lstrip('/')}"

                    download_pdf(full_url, pdf_path)

                    # Create URL for browser
                    relative_path = pdf_path.replace(str(PDFS_ROOT), "").lstrip("/")
                    pdf_url = f"{SERVER_URL}/pdfs/{relative_path}"
                    parts.append(f'<a href="{pdf_url}" target="_blank">{link_text}</a>')

                cols.append("<br>".join(parts) if parts else "")

            if cols:
                rows.append(cols)

    return rows


@router.get("/bombay", response_class=HTMLResponse)
async def bombay_form(request: Request):
    return templates.TemplateResponse("bombay_form.html", {"request": request})


@router.post("/bombay/results", response_class=HTMLResponse)
async def bombay_results(
    request: Request,
    m_bench: Optional[str] = Form(None),
    m_sideflg: Optional[str] = Form(None),
    m_skey: Optional[List[str]] = Form(None),
    m_no: Optional[str] = Form(None),
    m_yr: Optional[str] = Form(None),
    ncitation1: Optional[str] = Form(None),
    ncitation2: Optional[str] = Form(None),
    ncitation4: Optional[str] = Form(None),
    from_date: Optional[str] = Form(None),
    to_date: Optional[str] = Form(None),
    coram: Optional[str] = Form(None),
    actcode: Optional[List[str]] = Form(None),
    actside: Optional[str] = Form(None),
    party: Optional[str] = Form(None),
    party_p: Optional[str] = Form(None),
    joflag: Optional[List[str]] = Form(None),
    bflag: Optional[List[str]] = Form(None),
):
    from_date_fmt = convert_date_format(from_date) if from_date else ""
    to_date_fmt = convert_date_format(to_date) if to_date else ""

    today_folder = PDF_DIR / datetime.now().strftime("%Y-%m-%d")

    folder_name_parts = []
    form_fields = {
        "coram": coram,
        "actcode": ','.join(actcode) if actcode else None,
        "m_bench": m_bench,
        "m_skey": ','.join(m_skey) if m_skey else None,
        "m_sideflg": m_sideflg,
        "joflag": ','.join(joflag) if joflag else None,
        "bflag": ','.join(bflag) if bflag else None,
        "m_no": m_no,
        "m_yr": m_yr,
        "actside": actside,
        "party": party,
        "ncitation1": ncitation1,
        "ncitation2": ncitation2,
        "ncitation4": ncitation4,
    }

    for key, val in form_fields.items():
        if val:
            folder_name_parts.append(f"{key}={sanitize_filename(str(val))}")

    if from_date_fmt or to_date_fmt:
        folder_name_parts.append(f"from={from_date_fmt}_to={to_date_fmt}")

    subfolder_name = "__".join(folder_name_parts) or "search_results"
    save_folder = today_folder / subfolder_name
    os.makedirs(save_folder, exist_ok=True)

    rows = get_bombay_data(
        save_folder,
        from_date_fmt,
        to_date_fmt,
        coram,
        ','.join(actcode) if actcode else None,
        actside,
        m_bench,
        ','.join(m_skey) if m_skey else None,
        m_sideflg,
        joflag,
        bflag,

        m_no,
        m_yr,
        party,
        ncitation1,
        ncitation2,
        ncitation4,
    )

    rendered_html = templates.get_template("bombay_results.html").render(
        request=request, rows=rows, from_date=from_date_fmt, to_date=to_date_fmt
    )

    result_html_path = os.path.join(save_folder, "result.html")
    with open(result_html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    return HTMLResponse(rendered_html)


@router.get("/autocomplete/caseType", response_class=JSONResponse)
async def autocomplete_justice(q: str = Query(...)):
    url = f"https://bombayhighcourt.nic.in/ajax/get_skey.php?m_bench=&m_sideflg=&q={q}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return [s.strip() for s in response.text.strip().split("\n") if s.strip()]


@router.get("/autocomplete/act", response_class=JSONResponse)
async def autocomplete_act(q: str = Query(...)):
    url = f"https://bombayhighcourt.nic.in/ajax/get_act.php?m_bench=C&q={q}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        return [s.strip() for s in response.text.strip().split("\n") if s.strip()]
