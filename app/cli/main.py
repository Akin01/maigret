import asyncio
import logging
import typer
from rich.console import Console
from app.maigret.sites import MaigretDatabase
from app.maigret.checking import maigret as search
from app.maigret.notify import QueryNotifyPrint as Notifier

# Default values from wizard.py
TOP_SITES_COUNT = 300
TIMEOUT = 10
MAX_CONNECTIONS = 50

app = typer.Typer()
console = Console()

async def run_search(username, sites_count, timeout, max_connections, show_progressbar, extract_info, use_notifier):
    logger = logging.getLogger('maigret')
    logger.setLevel(logging.WARNING)

    db_path = 'app/maigret/resources/maigret.db'
    db = await MaigretDatabase().load_from_path(db_path)
    sites = db.ranked_sites_dict(top=sites_count)

    console.print(f"Searching for [bold green]{username}[/bold green] on {sites_count} sites.")

    notifier = None
    if use_notifier:
        notifier = Notifier(print_found_only=True, skip_check_errors=True)

    results = await search(
        username=username,
        site_dict=sites,
        timeout=timeout,
        logger=logger,
        max_connections=max_connections,
        query_notify=notifier,
        no_progressbar=not show_progressbar,
        is_parsing_enabled=extract_info,
    )

    console.print("\n[bold]Search Complete![/bold]")
    for sitename, data in results.items():
        is_found = data['status'].is_found()
        if is_found:
            console.print(f"[green]✔[/green] {sitename}: [bold green]Found![/bold green] - {data.get('url_user', 'N/A')}")
        else:
            console.print(f"[red]✖[/red] {sitename}: [red]Not Found[/red]")

@app.command()
def search_username(
    username: str = typer.Argument(..., help="Username to search"),
    sites_count: int = typer.Option(TOP_SITES_COUNT, "--sites-count", "-c", help=f"Number of sites to search (default: {TOP_SITES_COUNT})"),
    timeout: int = typer.Option(TIMEOUT, "--timeout", "-t", help=f"Request timeout (default: {TIMEOUT})"),
    max_connections: int = typer.Option(MAX_CONNECTIONS, "--max-connections", "-m", help=f"Max concurrent connections (default: {MAX_CONNECTIONS})"),
    show_progressbar: bool = typer.Option(True, "--progress/--no-progress", help="Show progress bar"),
    extract_info: bool = typer.Option(True, "--extract-info/--no-extract-info", help="Extract additional info from pages"),
    use_notifier: bool = typer.Option(True, "--notify/--no-notify", help="Use notifier for real-time results"),
):
    """
    Search for a username across multiple social networks.
    """
    asyncio.run(run_search(username, sites_count, timeout, max_connections, show_progressbar, extract_info, use_notifier))


if __name__ == "__main__":
    app()