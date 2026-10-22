"""
run_agent.py
============
Fully automated end-to-end runner:
  1. Connects to Chrome via CDP
  2. Navigates to Mahajana Sampatha ticket page (clicks Quick Buy automatically)
  3. Adds 10 tickets to cart
  4. Scores them via predictor.py
  5. Removes the 8 lowest-scoring tickets
  6. Pauses for manual payment
"""
import sys
import time
import re
import random

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console(highlight=False)

CDP_URL      = "http://localhost:9222"
LOTTERY_NAME = "Mahajana Sampatha"
TARGET       = 10   # tickets to add
KEEP         = 2    # tickets to keep after scoring

try:
    from predictor import score_tickets
    HAS_PREDICTOR = True
except ImportError:
    HAS_PREDICTOR = False
    console.print("[yellow]predictor.py not found - will skip scoring[/yellow]")

# ---------------------------------------------------------------------------
def navigate_to_ticket_page(page) -> bool:
    """
    Navigate to the Mahajana Sampatha ticket selection page.
    Uses JavaScript to find and click the correct Quick Buy button.
    Returns True if successful.
    """
    console.print("[cyan]Step 1: Navigating to Mahajana Sampatha ticket page...[/cyan]")

    # Check if already on ticket page (Add buttons visible)
    try:
        add_btns = page.locator("button.from-primaryColor").all()
        add_texts = [b.inner_text().strip() for b in add_btns]
        if any(t == "Add" for t in add_texts):
            console.print("[green]Already on ticket selection page.[/green]")
            return True
    except Exception:
        pass

    # Use JavaScript to find and click Quick Buy for Mahajana Sampatha
    result = page.evaluate("""() => {
        // Find all elements containing 'Mahajana Sampatha' text
        const allEls = Array.from(document.querySelectorAll('*'));
        for (const el of allEls) {
            if (el.children.length > 0) continue; // skip containers, only leaf nodes
            const txt = (el.innerText || el.textContent || '').trim();
            if (txt.toLowerCase().includes('mahajana') || txt.toLowerCase().includes('sampatha')) {
                // Walk up to find a container with a button
                let container = el;
                for (let i = 0; i < 8; i++) {
                    container = container.parentElement;
                    if (!container) break;
                    const btns = container.querySelectorAll('button');
                    for (const btn of btns) {
                        const btnTxt = (btn.innerText || '').trim().toLowerCase();
                        if (btnTxt.includes('quick buy') || btnTxt.includes('buy')) {
                            btn.click();
                            return { success: true, text: btn.innerText.trim() };
                        }
                    }
                }
            }
        }
        // Fallback: try clicking any button near lottery name via text search
        const buttons = Array.from(document.querySelectorAll('button'));
        for (const btn of buttons) {
            const ancestor = btn.closest('div') || btn.parentElement;
            if (!ancestor) continue;
            const txt = (ancestor.innerText || '').toLowerCase();
            if ((txt.includes('mahajana') || txt.includes('sampatha')) 
                && (btn.innerText.toLowerCase().includes('buy') || btn.innerText.toLowerCase().includes('quick'))) {
                btn.click();
                return { success: true, text: btn.innerText.trim(), method: 'fallback' };
            }
        }
        return { success: false };
    }""")

    console.print(f"[dim]JS click result: {result}[/dim]")

    if result and result.get("success"):
        page.wait_for_timeout(2500)
        # Verify we're on ticket page
        try:
            page.locator("button.from-primaryColor").first.wait_for(state="visible", timeout=5000)
            add_btns = page.locator("button.from-primaryColor").all()
            add_texts = [b.inner_text().strip() for b in add_btns]
            if any(t == "Add" for t in add_texts):
                console.print(f"[green]Ticket page loaded. 'Add' buttons visible.[/green]")
                return True
        except Exception:
            pass

    # Last resort: ask user to navigate manually, wait up to 60s
    console.print(
        "[yellow]Could not auto-navigate. Please click 'Quick Buy' on "
        "Mahajana Sampatha in your Chrome browser now.[/yellow]"
    )
    console.print("[yellow]Waiting up to 60 seconds...[/yellow]")
    for i in range(60):
        page.wait_for_timeout(1000)
        try:
            add_btns = page.locator("button.from-primaryColor").all()
            add_texts = [b.inner_text().strip() for b in add_btns]
            if any(t == "Add" for t in add_texts):
                console.print(f"[green]Ticket page detected after {i+1}s.[/green]")
                return True
        except Exception:
            pass
        if i % 10 == 9:
            console.print(f"[dim]Still waiting... ({i+1}s)[/dim]")

    return False


# ---------------------------------------------------------------------------
def add_tickets(page, target: int = 10) -> list[str]:
    """Add tickets to cart. Returns list of ticket identifiers added."""
    console.rule("[bold cyan]Phase 1 - Adding tickets to cart[/bold cyan]")
    added_tickets = []
    page_num = 1
    start = time.time()

    while len(added_tickets) < target:
        elapsed = int(time.time() - start)
        if elapsed > 240:
            console.print("[yellow]Time limit reached.[/yellow]")
            break

        console.print(f"\n[bold]Page {page_num}[/bold] | Cart: {len(added_tickets)}/{target} | {elapsed}s elapsed")

        # Get all Add buttons on current page (excluding Buy Tickets / Quick Buy)
        try:
            all_btns = page.locator("button.from-primaryColor").all()
            add_btns = [b for b in all_btns if b.inner_text().strip() == "Add"]
        except Exception:
            add_btns = []

        if not add_btns:
            # Try XPath fallback
            try:
                xpath_btns = page.locator("xpath=//button[normalize-space(text())='Add']").all()
                add_btns = xpath_btns
            except Exception:
                add_btns = []

        console.print(f"  Found {len(add_btns)} 'Add' button(s)")

        if not add_btns:
            console.print("[red]No Add buttons found. Stopping.[/red]")
            break

        # Pick one at random and extract its ticket info
        chosen_btn = random.choice(add_btns)

        # Get ticket identity from parent card
        ticket_id = "UNKNOWN"
        try:
            # Walk up to find the card div and extract text
            card_text = page.evaluate("""(btn) => {
                let el = btn;
                for (let i = 0; i < 6; i++) {
                    el = el.parentElement;
                    if (!el) break;
                    const txt = el.innerText || '';
                    // Card text looks like: "1\\nO\\nW\\n18\\n58\\n68\\n77\\n\\nAdd"
                    if (txt.includes('\\n') && txt.toLowerCase().includes('add')) {
                        return txt;
                    }
                }
                return '';
            }""", chosen_btn.element_handle())
            if card_text:
                parts = [p.strip() for p in card_text.split("\n") if p.strip()]
                parts = [p for p in parts if p.lower() not in ("add", "added", "adding")]
                if parts and parts[0].isdigit() and len(parts[0]) == 1:
                    parts = parts[1:]
                ticket_id = " ".join(parts)
        except Exception:
            pass

        console.print(f"  Selecting ticket: [bold]{ticket_id}[/bold]")

        # Click Add
        try:
            chosen_btn.scroll_into_view_if_needed()
            chosen_btn.click()
            page.wait_for_timeout(1800)
            added_tickets.append(ticket_id)
            console.print(f"  [green]Added! Cart now has {len(added_tickets)} ticket(s)[/green]")
        except Exception as e:
            console.print(f"  [red]Click failed: {e}[/red]")

        # Click "Show Next 5 Tickets" to get fresh tickets
        try:
            next_btn = page.locator("button:has-text('Show Next 5 Tickets')")
            next_btn.first.wait_for(state="visible", timeout=3000)
            next_btn.first.click()
            page.wait_for_timeout(1500)
            page_num += 1
            console.print(f"  [dim]Moved to next set of tickets[/dim]")
        except Exception:
            # No next button or already at end — still fine if we've added enough
            if len(added_tickets) < target:
                console.print(f"  [dim]No 'Show Next 5' button found[/dim]")

    return added_tickets


# ---------------------------------------------------------------------------
def go_to_cart(page) -> list[str]:
    """Navigate to cart and scrape ticket numbers."""
    console.rule("[bold cyan]Phase 2 - Reading cart[/bold cyan]")

    # Click cart icon in header
    try:
        # Cart icon is typically a button in the header with no text (icon only)
        cart_btns = page.locator("header button").all()
        for btn in cart_btns:
            txt = btn.inner_text().strip()
            if not txt:  # icon-only button = likely cart
                btn.click()
                page.wait_for_timeout(2000)
                break
    except Exception as e:
        console.print(f"[yellow]Cart click warning: {e}[/yellow]")

    # Also try navigating directly
    try:
        page.goto("https://winway.lk/cart", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)
    except Exception:
        pass

    console.print(f"Cart URL: {page.url}")

    # Extract ticket numbers from cart using JS
    cart_items = page.evaluate("""() => {
        const items = [];
        // Look for remove buttons and get their parent card text
        const removeBtns = Array.from(document.querySelectorAll('button')).filter(b => {
            const t = (b.innerText || '').toLowerCase();
            return t.includes('remove') || t.includes('delete');
        });
        for (const btn of removeBtns) {
            let el = btn;
            for (let i = 0; i < 6; i++) {
                el = el.parentElement;
                if (!el) break;
                const txt = (el.innerText || '').trim();
                if (txt.length > 5 && txt.length < 200) {
                    items.push(txt);
                    break;
                }
            }
        }
        // Fallback: get all text blocks that look like tickets
        if (items.length === 0) {
            const allDivs = Array.from(document.querySelectorAll('div, tr, li'));
            for (const div of allDivs) {
                if (div.children.length > 1 && div.children.length < 10) {
                    const txt = (div.innerText || '').trim();
                    if (txt.length > 3 && txt.length < 100) {
                        items.push(txt);
                    }
                }
            }
        }
        return items.slice(0, 20);
    }""")

    console.print(f"Cart items found: {len(cart_items)}")
    for i, item in enumerate(cart_items[:10]):
        safe = item.encode('ascii','replace').decode()[:60]
        console.print(f"  [{i+1}] {safe}")

    return cart_items


# ---------------------------------------------------------------------------
def score_and_remove(page, tickets: list[str], keep: int = 2) -> list[str]:
    """Score tickets, keep top N, remove the rest."""
    console.rule("[bold cyan]Phase 3 - Scoring & removing[/bold cyan]")

    if not tickets:
        console.print("[red]No tickets to score.[/red]")
        return []

    if not HAS_PREDICTOR:
        console.print("[yellow]No predictor — keeping first 2 tickets.[/yellow]")
        return tickets[:keep]

    try:
        ranked = score_tickets(LOTTERY_NAME, tickets)
    except Exception as e:
        console.print(f"[red]Scoring error: {e}. Keeping first {keep}.[/red]")
        return tickets[:keep]

    table = Table(title="Ticket Rankings", show_lines=True)
    table.add_column("Rank", justify="center", style="dim")
    table.add_column("Ticket", style="cyan")
    table.add_column("Score", justify="right", style="green")
    table.add_column("Action", justify="center")

    to_keep   = [t for t, _ in ranked[:keep]]
    to_remove = [t for t, _ in ranked[keep:]]

    for i, (ticket, score) in enumerate(ranked, 1):
        action = "[green]KEEP[/green]" if ticket in to_keep else "[red]REMOVE[/red]"
        table.add_row(str(i), ticket[:30], f"{score:.2f}", action)

    console.print(table)
    console.print(f"\nKeeping: [green]{to_keep}[/green]")
    console.print(f"Removing: [red]{to_remove}[/red]")

    # Remove tickets from cart via JS
    for ticket in to_remove:
        console.print(f"\nRemoving: [red]{ticket}[/red]")
        try:
            removed = page.evaluate("""(targetText) => {
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const btn of buttons) {
                    const t = (btn.innerText || '').toLowerCase();
                    if (!t.includes('remove') && !t.includes('delete')) continue;
                    // Check if this button's card contains our ticket
                    let el = btn;
                    for (let i = 0; i < 6; i++) {
                        el = el.parentElement;
                        if (!el) break;
                        const cardTxt = (el.innerText || '').toLowerCase();
                        if (cardTxt.includes(targetText.toLowerCase().substring(0, 8))) {
                            btn.click();
                            return true;
                        }
                    }
                }
                return false;
            }""", ticket)

            if removed:
                console.print(f"  [green]Removed.[/green]")
                page.wait_for_timeout(1200)
            else:
                console.print(f"  [yellow]Could not find remove button for this ticket.[/yellow]")
        except Exception as e:
            console.print(f"  [red]Remove error: {e}[/red]")

    return to_keep


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
console.print(Panel.fit(
    f"[bold cyan]Winway Automation Agent[/bold cyan]\n"
    f"Lottery: {LOTTERY_NAME} | Target: {TARGET} tickets | Keep: {KEEP}",
    title="Starting"
))

with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    context = browser.contexts[0]
    page    = context.pages[0]

    console.print(f"[green]Connected.[/green] URL: {page.url}")

    # Phase 0: Navigate to ticket page
    ok = navigate_to_ticket_page(page)
    if not ok:
        console.print("[red]Failed to reach ticket page. Exiting.[/red]")
        sys.exit(1)

    # Phase 1: Add 10 tickets
    added = add_tickets(page, target=TARGET)

    if not added:
        console.print("[red]No tickets were added. Exiting.[/red]")
        sys.exit(1)

    console.print(f"\n[green]Added {len(added)} tickets total.[/green]")

    # Phase 2: Go to cart
    cart_items = go_to_cart(page)

    # Use added list if cart scrape fails
    tickets_to_score = cart_items if cart_items else added

    # Phase 3: Score and remove
    kept = score_and_remove(page, tickets_to_score, keep=KEEP)

    # Done
    console.rule("[bold green]Complete[/bold green]")
    console.print(Panel.fit(
        f"[green]Done![/green]\n\n"
        f"[bold]Tickets kept ({len(kept)}):[/bold]\n"
        + "\n".join(f"  - {t}" for t in kept)
        + "\n\n[yellow]Complete payment manually in Chrome, then press Enter.[/yellow]",
        title="Results"
    ))

    try:
        input("\nPress Enter to exit...")
    except (EOFError, KeyboardInterrupt):
        pass
