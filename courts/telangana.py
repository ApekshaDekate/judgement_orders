from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import requests
from bs4 import BeautifulSoup
from PIL import Image
from io import BytesIO
import pytesseract
import numpy as np
import cv2
import time

router = APIRouter()
templates = Jinja2Templates(directory="templates")

BASE_URL = "https://tshc.gov.in/ehcr"
session = requests.Session()


@router.get("/telangana", response_class=HTMLResponse)
async def telangana_form(request: Request):
    # scrape judge and act options from Telangana HC site
    r = session.get(f"{BASE_URL}/judgewise")
    soup = BeautifulSoup(r.text, "html.parser")
    # judge dropdown
    judge_list = []
    judge_select = soup.find("select", {"name": "judge"})
    if judge_select:
        for opt in judge_select.find_all("option"):
            judge_list.append({"value": opt.get("value"), "label": opt.text.strip()})

    # for act search page
    r_act = session.get(f"{BASE_URL}/act")
    soup_act = BeautifulSoup(r_act.text, "html.parser")
    act_list = []
    act_select = soup_act.find("select", {"name": "act"})
    if act_select:
        for opt in act_select.find_all("option"):
            act_list.append({"value": opt.get("value"), "label": opt.text.strip()})

    return templates.TemplateResponse(
        "telangana_form.html",
        {
            "request": request,
            "judge_list": judge_list,
            "act_list": act_list,
        },
    )

@router.post("/telangana/results", response_class=HTMLResponse)
async def telangana_results(
    request: Request,
    search_type: str = Form(...),
    from_date: str = Form(None),
    to_date: str = Form(None),
    status: str = Form(None),  # default BOTH
    act: str = Form(None),
    section: str = Form(None),
    judge: str = Form(None),
    bench_code: str = Form(None)
):
    """
    Handle Telangana High Court search POST request.
    """
    # Choose the URL & payload based on search_type
    if search_type == "date":
        post_url = f"{BASE_URL}/orderdate"
        r = session.get(post_url)
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_token = soup.find("input", {"name": "_csrf"})["value"]

        payload = {
            "_csrf": csrf_token,
            "fromdt": from_date,
            "todt": to_date,
            "status": status ,
            "captcha": ""
        }

    elif search_type == "act":
        form_url = f"{BASE_URL}/act"
        post_url = f"{BASE_URL}/actsearch"
        r = session.get(form_url)
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_token = soup.find("input", {"name": "_csrf"})["value"]

        payload = {
            "_csrf": csrf_token,
            "act": act,
            "section": section,
            "fromdt": from_date,
            "todt": to_date,
            "judge": judge,
            "captcha": "",
        }

    elif search_type == "judge":
        form_url = f"{BASE_URL}/judgewise"            # URL with CSRF
        post_url = f"{BASE_URL}/judge"    # URL to post actual search

        r = session.get(form_url)
        soup = BeautifulSoup(r.text, "html.parser")

        # csrf_input = soup.find("input", {"name": "_csrf"})
        # csrf_token = csrf_input.get("value") if csrf_input else None
        csrf_token = soup.find("input", {"name": "_csrf"})["value"]

        

        payload = {
            "_csrf": csrf_token,
            "type": "J",
            "fromdt": from_date,
            "todt": to_date,
            "judge": judge,
            "captcha": "",
        }

    elif search_type == "bench":
        post_url = f"{BASE_URL}/judge"
        r = session.get(post_url)
        soup = BeautifulSoup(r.text, "html.parser")
        csrf_token = soup.find("input", {"name": "_csrf"})["value"]

        payload = {
            "_csrf": csrf_token,
            "type": "B",
            "fromdt": from_date,
            "todt": to_date,
            "judge": judge,
            "bench": bench_code,
            "status": status or "B",
            "captcha": "",
        }

    else:
        return templates.TemplateResponse(
            "telangana_results.html",
            {"request": request, "column_headers": [], "results": []}
        )

    # Post the form
    resp = session.post(post_url, data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"},timeout=200)
    time.sleep(10)  
    soup = BeautifulSoup(resp.text, "html.parser")

    table = soup.find("table", {"id": "reportTable"})
    headers = []
    rows = []
    if table:
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        for tr in table.find_all("tr")[1:]:
            tds = tr.find_all("td")
            if not tds:
                continue
            row_data = []
            for td in tds:
                link = td.find("a", href=True)
                if link:
                    # keep clickable HTML instead of just “View”
                    row_data.append(f'<a href="{link["href"]}" target="_blank">{link.get_text(strip=True)}</a>')
                else:
                    row_data.append(td.get_text(strip=True))
            rows.append(dict(zip(headers, row_data)))

    return templates.TemplateResponse(
        "telangana_results.html",
        {
            "request": request,
            "headers": headers,
            "results": rows
        }
    )