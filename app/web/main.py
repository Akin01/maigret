import asyncio
import logging
import os
from datetime import datetime
from threading import Thread
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from app.maigret.sites import MaigretDatabase
from app.maigret.checking import maigret as search
from app.maigret.report import generate_report_context, save_csv_report, save_json_report, save_pdf_report, save_html_report, save_graph_report
from app.maigret.result import MaigretCheckStatus
import uvicorn

# Configuration
MAIGRET_DB_FILE = os.path.join('app', 'maigret', 'resources', 'maigret.db')
COOKIES_FILE = "cookies.txt"
REPORTS_FOLDER = os.path.abspath('/tmp/maigret_reports')

app = FastAPI()

app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
templates = Jinja2Templates(directory="app/web/templates")

background_jobs = {}
job_results = {}

def setup_logger(log_level, name):
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    return logger

async def maigret_search(username, options):
    logger = setup_logger(logging.WARNING, 'maigret')
    try:
        db = await MaigretDatabase().load_from_path(MAIGRET_DB_FILE)

        top_sites = int(options.get('top_sites') or 500)
        if options.get('all_sites'):
            top_sites = 999999999

        tags = options.get('tags', [])
        site_list = options.get('site_list', [])

        sites = db.ranked_sites_dict(
            top=top_sites,
            tags=tags,
            names=site_list,
            disabled=False,
            id_type='username',
        )

        results = await search(
            username=username,
            site_dict=sites,
            timeout=int(options.get('timeout', 30)),
            logger=logger,
            id_type='username',
            cookies=COOKIES_FILE if options.get('use_cookies') else None,
            is_parsing_enabled=(not options.get('disable_extracting', False)),
            recursive_search_enabled=(
                not options.get('disable_recursive_search', False)
            ),
            check_domains=options.get('with_domains', False),
            proxy=options.get('proxy', None),
            tor_proxy=options.get('tor_proxy', None),
            i2p_proxy=options.get('i2p_proxy', None),
        )
        return results
    except Exception as e:
        logger.error(f"Error during search: {str(e)}")
        raise

async def search_multiple_usernames(usernames, options):
    results = []
    for username in usernames:
        try:
            search_results = await maigret_search(username.strip(), options)
            results.append((username.strip(), 'username', search_results))
        except Exception as e:
            logging.error(f"Error searching username {username}: {str(e)}")
    return results

def process_search_task(usernames, options, timestamp):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        general_results = loop.run_until_complete(
            search_multiple_usernames(usernames, options)
        )

        os.makedirs(REPORTS_FOLDER, exist_ok=True)
        session_folder = os.path.join(REPORTS_FOLDER, f"search_{timestamp}")
        os.makedirs(session_folder, exist_ok=True)

        graph_path = os.path.join(session_folder, "combined_graph.html")
        db = loop.run_until_complete(MaigretDatabase().load_from_path(MAIGRET_DB_FILE))
        save_graph_report(graph_path, general_results, db)

        individual_reports = []
        for username, id_type, results in general_results:
            report_base = os.path.join(session_folder, f"report_{username}")

            csv_path = f"{report_base}.csv"
            json_path = f"{report_base}.json"
            pdf_path = f"{report_base}.pdf"
            html_path = f"{report_base}.html"

            context = generate_report_context(general_results)

            save_csv_report(csv_path, username, results)
            save_json_report(json_path, username, results, report_type='ndjson')
            save_pdf_report(pdf_path, context)
            save_html_report(html_path, context)

            claimed_profiles = []
            for site_name, site_data in results.items():
                if (
                    site_data.get('status')
                    and site_data['status'].status == MaigretCheckStatus.CLAIMED
                ):
                    claimed_profiles.append(
                        {
                            'site_name': site_name,
                            'url': site_data.get('url_user', ''),
                            'tags': (
                                site_data.get('status').tags
                                if site_data.get('status')
                                else []
                            ),
                        }
                    )

            individual_reports.append(
                {
                    'username': username,
                    'csv_file': os.path.join(f"search_{timestamp}", f"report_{username}.csv"),
                    'json_file': os.path.join(f"search_{timestamp}", f"report_{username}.json"),
                    'pdf_file': os.path.join(f"search_{timestamp}", f"report_{username}.pdf"),
                    'html_file': os.path.join(f"search_{timestamp}", f"report_{username}.html"),
                    'claimed_profiles': claimed_profiles,
                }
            )

        job_results[timestamp] = {
            'status': 'completed',
            'session_folder': f"search_{timestamp}",
            'graph_file': os.path.join(f"search_{timestamp}", "combined_graph.html"),
            'usernames': usernames,
            'individual_reports': individual_reports,
        }

    except Exception as e:
        logging.error(f"Error in search task for timestamp {timestamp}: {str(e)}")
        job_results[timestamp] = {'status': 'failed', 'error': str(e)}
    finally:
        background_jobs[timestamp]['completed'] = True

@app.get("/", response_class=HTMLResponse, name="index")
async def index(request: Request):
    db = await MaigretDatabase().load_from_path(MAIGRET_DB_FILE)
    site_options = sorted(set([site.name for site in db.sites] + [site.url_main for site in db.sites if site.url_main]))
    return templates.TemplateResponse("index.html", {"request": request, "site_options": site_options})

@app.post("/search", name="search")
async def search_post(request: Request):
    form = await request.form()
    usernames_input = form.get('usernames', '').strip()
    if not usernames_input:
        return RedirectResponse(url=request.url_for('index'), status_code=303)

    usernames = [u.strip() for u in usernames_input.replace(',', ' ').split() if u.strip()]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    selected_tags = form.getlist('tags')

    options = {
        'top_sites': form.get('top_sites') or '500',
        'timeout': form.get('timeout') or '30',
        'use_cookies': 'use_cookies' in form,
        'all_sites': 'all_sites' in form,
        'disable_recursive_search': 'disable_recursive_search' in form,
        'disable_extracting': 'disable_extracting' in form,
        'with_domains': 'with_domains' in form,
        'proxy': form.get('proxy', None) or None,
        'tor_proxy': form.get('tor_proxy', None) or None,
        'i2p_proxy': form.get('i2p_proxy', None) or None,
        'permute': 'permute' in form,
        'tags': selected_tags,
        'site_list': [s.strip() for s in form.get('site', '').split(',') if s.strip()],
    }

    thread = Thread(target=process_search_task, args=(usernames, options, timestamp))
    background_jobs[timestamp] = {'completed': False, 'thread': thread}
    thread.start()

    return RedirectResponse(url=request.url_for('status', timestamp=timestamp), status_code=303)

@app.get("/status/{timestamp}", response_class=HTMLResponse, name="status")
async def status(request: Request, timestamp: str):
    if timestamp not in background_jobs:
        raise HTTPException(status_code=404, detail="Invalid search session.")

    if background_jobs[timestamp]['completed']:
        result = job_results.get(timestamp)
        if not result:
            raise HTTPException(status_code=404, detail="No results found for this search session.")

        if result['status'] == 'completed':
            return RedirectResponse(url=request.url_for('results', session_id=result['session_folder']), status_code=303)
        else:
            return templates.TemplateResponse("error.html", {"request": request, "error_message": result.get('error', 'Unknown error occurred.')})

    return templates.TemplateResponse("status.html", {"request": request, "timestamp": timestamp})

@app.get("/results/{session_id}", response_class=HTMLResponse, name="results")
async def results(request: Request, session_id: str):
    result_data = next(
        (r for r in job_results.values() if r.get('status') == 'completed' and r['session_folder'] == session_id),
        None,
    )

    if not result_data:
        raise HTTPException(status_code=404, detail="No results found for this session ID.")

    return templates.TemplateResponse(
        "results.html",
        {
            "request": request,
            "usernames": result_data['usernames'],
            "graph_file": result_data['graph_file'],
            "individual_reports": result_data['individual_reports'],
            "timestamp": session_id.replace('search_', ''),
        },
    )

@app.get("/reports/{path:path}", name="download_report")
async def download_report(path: str):
    file_path = os.path.normpath(os.path.join(REPORTS_FOLDER, path))
    if not file_path.startswith(REPORTS_FOLDER):
        raise HTTPException(status_code=403, detail="Invalid file path")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    uvicorn.run(app, host="127.0.0.1", port=8000)