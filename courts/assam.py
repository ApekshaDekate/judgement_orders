# file: courts/assam.py
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import requests
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from io import BytesIO
from bs4 import BeautifulSoup
import pytesseract
import re
from urllib.parse import quote, urljoin, unquote
from datetime import datetime
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# === Constants ===
ASSAM_BASE_URL = "https://hcservices.ecourts.gov.in/ecourtindiaHC/cases/"
ASSAM_CAPTCHA_URL = "https://hcservices.ecourts.gov.in/ecourtindiaHC/securimage/securimage_show.php?"
PDFS_ROOT = Path("/media/ibmarray2_1/airstorage/commpdf/")
PDFS_ROOT.mkdir(exist_ok=True)
PDF_DIR = PDFS_ROOT / "assam"
PDF_DIR.mkdir(exist_ok=True)

# persistent session to preserve cookies
session = requests.Session()
session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; scraper/1.0)"})

# Bench definitions: change court_code / form_url if site uses different params for your benches
ASSAM_BENCHES = {
    "guwahati": {
        "label": "Principal Seat - Guwahati",
        "court_code": "1",
        "form_url": "https://hcservices.ecourts.gov.in/ecourtindiaHC/index_highcourt.php?state_cd=6&dist_cd=1&stateNm=Assam"
    },
    "kohima": {
        "label": "Kohima Bench",
        "court_code": "2",
        "form_url": "https://hcservices.ecourts.gov.in/ecourtindiaHC/index_highcourt.php?state_cd=6&dist_cd=1&court_code=2&stateNm=Assam"
    },
    "aizwal": {
        "label": "Aizawl Bench",
        "court_code": "3",
        "form_url": "https://hcservices.ecourts.gov.in/ecourtindiaHC/index_highcourt.php?state_cd=6&dist_cd=1&court_code=3&stateNm=Assam"
    },
    "itanagar": {
        "label": "Itanagar Bench",
        "court_code": "4",
        "form_url": "https://hcservices.ecourts.gov.in/ecourtindiaHC/index_highcourt.php?state_cd=6&dist_cd=1&court_code=4&stateNm=Assam"
    },
}

# ----------------- Utilities (same robust approach as Andhra) -----------------
def fetch_assam_judges(court_code: str):
    """
    Fetch judge list for a given Assam bench (court_code).
    Same behaviour as your Andhra judge dropdown function.
    """
    url = f"{ASSAM_BASE_URL}s_order_qry.php"

    payload = {
        "__csrf_magic": "",
        "action_code": "fillJudges",
        "state_code": "6",
        "dist_code": "1",
        "court_code": court_code
    }

    try:
        res = session.post(url, data=payload, verify=False, timeout=15)
        res.raise_for_status()
        raw = res.text.strip()

        judges = []
        if raw:
            for part in raw.split("#"):
                if "~" in part:
                    value, label = part.split("~", 1)
                    judges.append({
                        "value": value.strip(),
                        "label": label.strip()
                    })

        return judges

    except Exception as e:
        print("Error fetching Assam judges:", e)
        return []


def safe_text(s: str) -> str:
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return s.encode("utf-8").decode("utf-8-sig").replace("\ufeff", "").strip()

def get_captcha(session: requests.Session) -> str:
    """Download captcha image and run OCR (best-effort)."""
    try:
        r = session.get(ASSAM_CAPTCHA_URL, verify=False, timeout=15)
        img_bytes = r.content
        arr = np.frombuffer(img_bytes, np.uint8)
        opencv_img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if opencv_img is None:
            return ""
        mask = cv2.inRange(opencv_img, (100, 100, 100), (255, 255, 255))
        opencv_img[mask > 0] = (255, 255, 255)
        pil = Image.fromarray(opencv_img)
        text = pytesseract.image_to_string(
            pil,
            config='--psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        ).strip()
        print("CAPTCHA:", text)
        return text
    except Exception as e:
        print("CAPTCHA error:", e)
        return ""

def find_pdf_candidate_from_html(html: str, base_url: str):
    """Search common elements for a real PDF link inside wrapper HTML."""
    soup = BeautifulSoup(html, "html.parser")
    embed = soup.find("embed")
    if embed and embed.get("src"):
        return urljoin(base_url, embed["src"])
    iframe = soup.find("iframe")
    if iframe and iframe.get("src"):
        return urljoin(base_url, iframe["src"])
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" in href.lower() or "display_pdf.php" in href:
            return urljoin(base_url, href)
    m = re.search(r"window\.location(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]", html, re.I)
    if m:
        return urljoin(base_url, m.group(1))
    meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if meta:
        content = meta.get("content", "")
        mm = re.search(r"url=(.*)", content, re.I)
        if mm:
            return urljoin(base_url, mm.group(1).strip())
    m2 = re.search(r"https?://[^\s'\"<>]+\.pdf", html, re.I)
    if m2:
        return m2.group(0)
    return None

def download_streaming(url: str, save_path: Path, referer: str = None) -> bool:
    """Stream download with magic-bytes validation."""
    try:
        headers = {"User-Agent": session.headers.get("User-Agent")}
        if referer:
            headers["Referer"] = referer
        with session.get(url, headers=headers, verify=False, timeout=40, stream=True) as r:
            if r.status_code != 200:
                print("Download failed status:", r.status_code, url)
                return False
            tmp = save_path.with_suffix(save_path.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            # Validate first bytes
            with open(tmp, "rb") as f:
                start = f.read(4)
            ctype = (r.headers.get("Content-Type") or "").lower()
            if start == b"%PDF" or "application/pdf" in ctype:
                tmp.replace(save_path)
                return True
            else:
                try:
                    tmp.unlink()
                except Exception:
                    pass
                print("Downloaded file is not PDF:", url, ctype)
                return False
    except Exception as e:
        print("Streaming download error:", e, url)
        return False

def download_assam_display(display_url: str, save_path: Path, referer_form_url: str = None) -> bool:
    """
    Download logic derived from your Andhra function — identical strategy:
      - GET display_url; if content-type PDF => save
      - else parse HTML for candidate PDF link and stream-download
      - fallback: stream display_url and check magic-bytes
    """
    try:
        headers = {"User-Agent": session.headers.get("User-Agent")}
        if referer_form_url:
            headers["Referer"] = referer_form_url
        resp = session.get(display_url, headers=headers, verify=False, timeout=25, allow_redirects=True)
    except Exception as e:
        print("Error requesting display page:", display_url, e)
        return False

    ctype = (resp.headers.get("Content-Type") or "").lower()
    if resp.status_code == 200 and "application/pdf" in ctype:
        try:
            save_path.write_bytes(resp.content)
            return True
        except Exception as e:
            print("Error saving direct PDF:", e, display_url)
            return False

    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
        try:
            save_path.write_bytes(resp.content)
            return True
        except Exception as e:
            print("Error saving pdf-by-magic:", e)
            return False

    html = resp.text or ""
    candidate = find_pdf_candidate_from_html(html, display_url)
    if candidate:
        candidate = unquote(candidate)
        return download_streaming(candidate, save_path, referer=display_url)

    # final fallback: stream the display_url and check magic bytes
    try:
        headers2 = {"User-Agent": session.headers.get("User-Agent")}
        if referer_form_url:
            headers2["Referer"] = referer_form_url
        with session.get(display_url, headers=headers2, verify=False, timeout=30, stream=True) as r2:
            tmp = save_path.with_suffix(save_path.suffix + ".part")
            with open(tmp, "wb") as f:
                for chunk in r2.iter_content(8192):
                    if chunk:
                        f.write(chunk)
            with open(tmp, "rb") as f:
                start = f.read(4)
            if start == b"%PDF" or "application/pdf" in (r2.headers.get("Content-Type") or "").lower():
                tmp.replace(save_path)
                return True
            try:
                tmp.unlink()
            except Exception:
                pass
    except Exception as e:
        print("Final fallback error:", e, display_url)

    print("No usable PDF candidate for:", display_url)
    return False

def make_pdf_filename_from_case(case_no: str) -> str:
    cn = safe_text(case_no)
    m = re.match(r"([^/\\]+)[/\\]([^/\\]+)[/\\]([0-9]{4})", cn)
    if m:
        type_part = re.sub(r"\W+", "_", m.group(1).upper())
        num_part = re.sub(r"\W+", "_", m.group(2))
        year_part = m.group(3)
        return f"{type_part}_{num_part}_{year_part}.pdf"
    s = re.sub(r"[^A-Za-z0-9_\-]+", "_", cn)
    if not s:
        s = "case_unknown"
    return f"{s}.pdf"

# ----------------- Routes -----------------
@router.get("/assam", response_class=HTMLResponse)
async def assam_form(request: Request):
    benches = [{"id": k, "label": v["label"]} for k, v in ASSAM_BENCHES.items()]

    # # load judges for default bench (guwahati)
    # default_bench = ASSAM_BENCHES["guwahati"]
    # judge_list = fetch_assam_judges(default_bench["court_code"])

    return templates.TemplateResponse(
        "assam_form.html",
        {"request": request, "benches": benches}
    )

@router.get("/assam/judges/{bench_id}")
async def get_assam_judges(bench_id: str):
    bench = ASSAM_BENCHES.get(bench_id)
    if not bench:
        return {"judges": []}
    judges = fetch_assam_judges(bench["court_code"])
    return {"judges": judges}


@router.post("/assam/results", response_class=HTMLResponse)
async def fetch_assam_results(request: Request):
    form = await request.form()

    # ---------- 1) Bench
    bench_id = form.get("bench_id") or form.get("bench") or "guwahati"
    bench = ASSAM_BENCHES.get(bench_id)
    if not bench:
        return HTMLResponse("Invalid bench", status_code=400)
    court_code = bench["court_code"]
    form_url = bench["form_url"]

    # ensure cookies by requesting the bench-specific form URL
    try:
        session.get(form_url, verify=False, timeout=10)
    except Exception:
        pass

    # ---------- 2) CAPTCHA
    captcha = get_captcha(session)
    if not captcha:
        return HTMLResponse("CAPTCHA failed (OCR)", status_code=500)

    # ---------- 3) Read form fields with flexible fallbacks (to tolerate different frontends)
    search_type = (form.get("search_type") or "date").strip()
    # date fields (common)
    from_date = (form.get("from_date") or form.get("date_from") or "").strip()
    to_date   = (form.get("to_date")   or form.get("date_to")   or "").strip()

    # judge fields: accept both nnjudgecode1 or judge or judge_code
    judge_code = (form.get("nnjudgecode1") or form.get("judge") or form.get("judge_code") or "").strip()
    judge_from_date = (form.get("judge_from_date") or form.get("from_date") or "").strip()
    judge_to_date   = (form.get("judge_to_date")   or form.get("to_date")   or "").strip()

    # party field: accept party_name / partyname / party
    party_name = (form.get("party_name") or form.get("partyname") or form.get("party") or "").strip()
    rgyear = (form.get("rgyear") or form.get("year") or "").strip()

    citation_no = (form.get("citation_no") or form.get("citation") or "").strip()

    # build search_label and payload
    post_url = None
    payload = {}

    if search_type == "judge" and judge_code:
        # use judge_from_date/judge_to_date if provided; else fall back to from_date/to_date
        d1 = judge_from_date or from_date
        d2 = judge_to_date or to_date
        search_label = f"judge_{judge_code}_{d1}_to_{d2}"
        post_url = f"{ASSAM_BASE_URL}s_order_qry.php"
        payload = {
            "__csrf_magic": "",
            "action_code": "showRecords",
            "state_code": "6",
            "dist_code": "1",
            "temp_date1": d1,
            "temp_date2": d2,
            "judge_code": judge_code,
            "captcha": captcha,
            "court_code": court_code,
            "reportableJudges": "All",
            "typeOfOrders": "0"
        }

    elif search_type == "party" and party_name:
        # remote expects parameter name "partyname" (not party_name)
        search_label = f"party_{party_name}_{rgyear}"
        post_url = f"{ASSAM_BASE_URL}s_partyorder_qry.php"
        payload = {
            "__csrf_magic": "",
            "action_code": "showRecords",
            "state_code": "6",
            "dist_code": "1",
            "captcha": captcha,
            "court_code": court_code,
            "partyname": party_name,
            "rgyear": rgyear
        }

    elif search_type == "citation" and citation_no:
        search_label = f"citation_{citation_no}"
        post_url = f"{ASSAM_BASE_URL}s_citation_qry.php"
        payload = {
            "__csrf_magic": "",
            "action_code": "showRecords",
            "state_code": "6",
            "dist_code": "1",
            "citation_no": citation_no,
            "captcha": captcha,
            "court_code": court_code
        }

    else:
        # default: date search (from_date/to_date)
        search_label = f"date_{from_date}_to_{to_date}"
        post_url = f"{ASSAM_BASE_URL}s_orderdate_qry.php"
        payload = {
            "__csrf_magic": "",
            "action_code": "showRecords",
            "state_code": "6",
            "dist_code": "1",
            "from_date": from_date,
            "to_date": to_date,
            "captcha": captcha,
            "court_code": court_code
        }

    # debug print (helps trace missing fields)
    print("==== RECEIVED FORM DATA ====")
    print("bench_id:", bench_id, ", bench:", bench)
    print("search_type:", search_type)
    print("from_date:", from_date, ", to_date:", to_date)
    print("judge_code:", judge_code, ", judge_from_date:", judge_from_date, ", judge_to_date:", judge_to_date)
    print("party_name:", party_name, ", rgyear:", rgyear)
    print("citation_no:", citation_no)
    print("payload keys:", list(payload.keys()))
    print("============================")

    # ---------- prepare folders
    safe_search_label = re.sub(r"[^A-Za-z0-9_\-]+", "_", (search_label or "search"))
    today_folder = PDF_DIR / bench_id / datetime.now().strftime("%Y-%m-%d")
    search_folder = today_folder / safe_search_label
    search_folder.mkdir(parents=True, exist_ok=True)

    # ---------- POST and raw response
    try:
        resp = session.post(post_url, data=payload, verify=False, timeout=40)
        raw_text = resp.text or ""
    except Exception as e:
        print("Error posting search:", e)
        raw_text = ""
    (search_folder / "raw_response.txt").write_text(raw_text, encoding="utf-8")

    # ---------- parse returned records
    if "##" in raw_text:
        records = [r for r in raw_text.split("##") if r.strip()]
    else:
        records = [r for r in raw_text.split("~2##") if r.strip()]

    data = []
    for idx, rec in enumerate(records, start=1):
        parts = rec.split("~")
        if len(parts) < 3:
            continue
        case_no = safe_text(parts[0])
        order_date = safe_text(parts[1]) if len(parts) > 1 else "unknown_date"
        filename_token = safe_text(parts[2]) if len(parts) > 2 else ""
        order_type = safe_text(parts[3]) if len(parts) > 3 else ""
        cino = safe_text(parts[8]) if len(parts) > 8 else ""
        # court code guess from returned record (sometimes present)
        court_code_guess = ""
        if len(parts) > 5 and parts[5].strip().isdigit():
            court_code_guess = parts[5].strip()
        elif len(parts) > 4 and parts[4].strip().isdigit():
            court_code_guess = parts[4].strip()
        else:
            court_code_guess = court_code

        # skip invalid tokens
        if not filename_token or filename_token == "N":
            print("❌ Invalid filename token for:", case_no, "token:", filename_token)
            data.append({
                "index": idx,
                "case_no": case_no,
                "order_date": order_date,
                "order_type": order_type,
                "pdf_link": "",
                "downloaded": False
            })
            continue

        # normalize token and build display URL
        try:
            token_unq = unquote(unquote(filename_token))
        except Exception:
            token_unq = filename_token
        filename_param = quote(token_unq, safe="")

        display_url = (
            f"{ASSAM_BASE_URL}display_pdf.php?"
            f"filename={filename_param}"
            f"&caseno={quote(case_no)}"
            f"&cCode={quote(court_code_guess)}"
            f"&cino={quote(cino)}"
            f"&state_code=6&appFlag="
        )

        # prepare path & download
        date_folder = search_folder / (order_date or "unknown_date")
        date_folder.mkdir(parents=True, exist_ok=True)
        pdf_name = make_pdf_filename_from_case(case_no)
        save_path = date_folder / pdf_name

        downloaded = False
        if not save_path.exists():
            downloaded = download_assam_display(display_url, save_path, referer_form_url=form_url)
        else:
            downloaded = True

        rel_link = f"./{order_date}/{pdf_name}" if downloaded else ""

        data.append({
            "index": idx,
            "case_no": case_no,
            "order_date": order_date,
            "order_type": order_type,
            "pdf_link": rel_link,
            "downloaded": downloaded
        })

    # render results page
    rendered_html = templates.get_template("assam_results.html").render(request=request, data=data, bench=bench)
    (search_folder / "results.html").write_text(rendered_html, encoding="utf-8")

    print(f"✅ Saved results HTML at: {search_folder / 'results.html'}")
    print(f"✅ PDFs stored in: {search_folder}")

    return HTMLResponse(rendered_html)