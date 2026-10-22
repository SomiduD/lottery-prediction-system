"""
winway_agent.py
================
Module 3 – Winway Playwright Agent

Connects to an EXISTING Chrome browser that you launched with remote
debugging enabled.  The script:

  1. Attaches to your logged-in Chrome session via CDP.
  2. Runs a timer-optimised loop (target: < 4 minutes):
       • Scans the current ticket page on winway.lk.
       • Reads the 5 available tickets, picks 1 at random, adds to cart.
       • Navigates to the next page and repeats until 10 tickets are in cart.
  3. Goes to the checkout/cart page.
  4. Scrapes the 10 ticket numbers from the cart.
  5. Passes them to predictor.py → gets ranked scores.
  6. Removes the 8 lowest-scoring tickets from the cart (keeps top 2).
  7. Pauses – you complete payment manually.

─────────────────────────────────────────────────────────────────────
HOW TO START CHROME WITH REMOTE DEBUGGING (run this first, once):

  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" ^
    --remote-debugging-port=9222 ^
    --user-data-dir="C:\\ChromeDebug" ^
    --no-first-run ^
    --no-default-browser-check

Then log in to winway.lk in that browser, navigate to any lottery ticket
purchase page, and run:

  python winway_agent.py

Or with options:
  python winway_agent.py --cdp-port 9222 --lottery "Mahajana Sampatha" --target-tickets 10 --keep 2
─────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Force UTF-8 output so Rich Unicode symbols work on Windows CP1252 terminals
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except AttributeError:
    pass

# ---------------------------------------------------------------------------
# Lazy import of predictor so the script works even if DB is missing
# (agent will warn rather than crash).
# ---------------------------------------------------------------------------
try:
    from predictor import score_tickets  # type: ignore
    _PREDICTOR_AVAILABLE = True
except ImportError:
    _PREDICTOR_AVAILABLE = False

console = Console(highlight=False)

# ---------------------------------------------------------------------------
# Playwright timeout constants (milliseconds)
# ---------------------------------------------------------------------------
PAGE_LOAD_TIMEOUT   = 30_000   # 30 s
ELEMENT_TIMEOUT     = 15_000   # 15 s
SHORT_WAIT          = 1_500    # 1.5 s – brief stabilisation pause
CART_ACTION_WAIT    = 2_000    # 2 s   – after add-to-cart click
REMOVE_ACTION_WAIT  = 1_000    # 1 s   – after each remove click

# ---------------------------------------------------------------------------
# Winway URL constants
# ---------------------------------------------------------------------------
BASE_URL       = "https://winway.lk"
CART_URL       = f"{BASE_URL}/cart"
CHECKOUT_URL   = f"{BASE_URL}/checkout"

# ---------------------------------------------------------------------------
# Selector catalogue  (tuned for winway.lk – TailwindCSS React SPA)
#
# winway.lk uses Tailwind utility classes only; there are NO custom
# semantic class names in its CSS bundle.  All selectors below therefore
# rely on:
#   1. data-* / aria-* / role attributes  (most stable)
#   2. Structural XPath text-matching       (second line of defence)
#   3. Generic structural fallbacks         (last resort)
#
# HOW TO UPDATE when the site changes:
#   1. Open DevTools (F12) on the target page.
#   2. Press Ctrl+Shift+C and click the element.
#   3. Right-click the highlighted HTML → Copy → Copy XPath.
#   4. Prepend the new selector to the relevant list below.
# ---------------------------------------------------------------------------

# ── Ticket rows on a lottery ticket page ──
# CONFIRMED from live CDP inspection (2026-09-28):
# Each ticket card is:
#   <div class="relative flex flex-wrap justify-center sm:justify-between items-center ...">
#     ... ticket number spans ...
#     <div class="flex-shrink-0"><button class="...from-primaryColor...">Add</button></div>
#   </div>
# NOTE: Tailwind class names have spaces between them, so CSS dot-chaining (div.flex.flex-wrap)
#       does NOT work in Playwright. Use XPath contains(@class, 'token') instead.
TICKET_ROW_SELECTORS = [
    # ── Level 1: data-* attributes ──
    "[data-testid='ticket-card']",
    "[data-testid='ticket-row']",
    # ── Level 2: CONFIRMED – XPath using confirmed class tokens ──
    # The card div contains 'flex-wrap' in its class AND has a button with 'from-primaryColor'
    "//div[contains(@class,'flex-wrap') and .//button[contains(@class,'from-primaryColor')]]",
    # ── Level 3: parent of flex-shrink-0 which wraps the Add button ──
    "//div[contains(@class,'flex-shrink-0') and ./button[contains(@class,'from-primaryColor')]]/parent::div",
    # ── Level 4: container div (p-3 space-y-3) children ──
    "//div[contains(@class,'space-y-3')]/div",
    # ── Level 5: generic fallbacks ──
    "//div[contains(@class,'rounded-2xl')]//div[.//button[contains(@class,'from-primaryColor')]]",
]

# ── Add-to-cart button inside a ticket row ──
# CONFIRMED: text='Add', class contains 'from-primaryColor'
# NOTE: CSS selector 'button.from-primaryColor' works because 'from-primaryColor'
#       is a single unspaced token, unlike compound Tailwind classes.
ADD_TO_CART_SELECTORS = [
    # ── Level 1: data-* / aria ──
    "[data-testid='add-to-cart-btn']",
    "[data-testid='buy-btn']",
    # ── Level 2: CONFIRMED single-token Tailwind class (works with CSS) ──
    "button.from-primaryColor",
    # ── Level 3: XPath class contains ──
    "//button[contains(@class,'from-primaryColor')]",
    # ── Level 4: exact text match (confirmed text is 'Add') ──
    "//button[normalize-space(text())='Add']",
    "button:has-text('Add')",
    # ── Level 5: generic primary colour fallbacks ──
    "button.bg-primaryColor",
    "button.bg-primary",
]

# ── Ticket number text within a row ──
# CONFIRMED from live CDP inspection (2026-09-28):
# Ticket numbers are NOT in a single element. They are individual text nodes:
#   Row text = "1\nO\nW\n18\n58\n68\n77\n\nAdd"
# The ticket identifier is the FULL row text (excluding the trailing 'Add').
# We extract it via inner_text() on the card div and strip the trailing button text.
# No single CSS selector can isolate it - use the _get_cart_ticket_number() approach
# which reads the whole card's inner_text and cleans it up.
TICKET_NUMBER_SELECTORS = [
    # ── Level 1: data-* ──
    "[data-testid='ticket-number']",
    "[data-ticket-number]",
    # ── Level 2: font-mono (serial display) ──
    "span.font-mono",
    "p.font-mono",
    "div.font-mono",
    # ── Level 3: font-bold spans ──
    "span.font-bold",
    "span.font-semibold",
    "span.tracking-widest",
    "span.tracking-wider",
    # ── Level 4: XPath – first short text span in the card ──
    "//span[string-length(normalize-space(text())) >= 1 and string-length(normalize-space(text())) <= 12]",
]

# ── Cart item rows ──
# winway.lk's cart page lists each ticket as a card/row.
CART_ITEM_SELECTORS = [
    # ── Level 1: data-* ──
    "[data-testid='cart-item']",
    "[data-testid='cart-ticket']",
    "[data-testid='cart-row']",
    # ── Level 2: aria ──
    "[role='row']",
    # ── Level 3: XPath – rows that contain a remove/delete button ──
    "//div[.//button[contains(text(),'Remove') or contains(text(),'Delete') or contains(@aria-label,'Remove') or contains(@aria-label,'Delete')]]",
    "//li[.//button[contains(text(),'Remove') or contains(text(),'Delete')]]",
    "//tr[.//button]",
    # ── Level 4: Tailwind structural ──
    "div.border-b:has(button)",
    "div.divide-y > div",
    "tbody > tr",
    "ul > li:has(button)",
    "tr.cart-row",
    "div[class*='cart-item']",
]

# ── Remove button inside a cart item ──
# winway.lk's remove action is typically a red/destructive button or icon button.
REMOVE_BUTTON_SELECTORS = [
    # ── Level 1: data-* / aria ──
    "[data-testid='remove-btn']",
    "[data-testid='delete-btn']",
    "[data-testid='remove-ticket']",
    "button[aria-label*='Remove' i]",
    "button[aria-label*='Delete' i]",
    "button[aria-label*='remove' i]",
    # ── Level 2: XPath text-match ──
    "//button[contains(text(),'Remove')]",
    "//button[contains(text(),'Delete')]",
    "//button[contains(text(),'remove')]",
    # ── Level 3: Tailwind colour-class (destructive = red) ──
    "button.bg-redColor",
    "button.bg-redDark",
    "button.bg-red-600",
    "button.bg-destructive",
    # ── Level 4: SVG trash-icon button (common pattern) ──
    "button:has(svg)",
    # ── Level 5: Playwright text-match ──
    "button:has-text('Remove')",
    "button:has-text('Delete')",
]

# ── Cart count badge (to verify count) ──
# The cart icon is in the header nav.  The badge is a small absolute-positioned
# span on top of the icon, styled with Tailwind (bg-primaryColor, rounded-full).
CART_COUNT_SELECTORS = [
    # ── Level 1: data-* ──
    "[data-testid='cart-count']",
    "[data-testid='cart-badge']",
    # ── Level 2: common Tailwind badge pattern ──
    # A badge is typically: span.absolute.-top-1.-right-1.rounded-full ...
    "span.rounded-full.bg-primaryColor",
    "span.rounded-full.bg-primary",
    "span.rounded-full.bg-mainColor",
    "span.-top-1.-right-1",
    "span.absolute.rounded-full",
    # ── Level 3: header nav badge ──
    "header span.rounded-full",
    "nav span.rounded-full",
]

# ── "Next page" / pagination on ticket listing ──
# CONFIRMED from live DOM (2026-09-28):
# Button text = 'Show Next 5 Tickets'
# class = 'border-2 border-gray-500 hover:border-gray-700 active:border-gray-700 ...'
NEXT_PAGE_SELECTORS = [
    # ── Level 1: CONFIRMED button text ──
    "button:has-text('Show Next 5 Tickets')",
    "//button[contains(text(),'Show Next 5 Tickets')]",
    "//button[contains(text(),'Next 5')]",
    # ── Level 2: data-* / aria ──
    "[data-testid='next-page']",
    "[data-testid='pagination-next']",
    "button[aria-label='Next page']",
    "a[rel='next']",
    # ── Level 3: generic text fallbacks ──
    "button:has-text('Next')",
    "//button[contains(text(),'Next')]",
    "a:has-text('Next')",
    # ── Level 4: border-gray-500 structural (confirmed class) ──
    "button.border-gray-500",
]


# ---------------------------------------------------------------------------
# Playwright helpers
# ---------------------------------------------------------------------------

def _connect_to_chrome(cdp_url: str):
    """
    Connect to an already-running Chrome instance via CDP.
    Returns (playwright, browser, page) – caller is responsible for cleanup.
    """
    from playwright.sync_api import sync_playwright

    console.print(f"[cyan]Connecting to Chrome at[/cyan] {cdp_url} …")
    pw      = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp_url)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page    = context.pages[0]    if context.pages    else context.new_page()

    console.print(
        f"[green]Connected.[/green] Current URL: [bold]{page.url}[/bold]"
    )
    return pw, browser, page


def _debug_dump_dom(page, output_path: str = "winway_dom_dump.html") -> None:
    """
    DEBUG HELPER: save the full rendered DOM of the current page to a file
    so you can inspect it and find the right selectors.

    Usage (interactive Python / inline in agent):
        _debug_dump_dom(page)
    Then open winway_dom_dump.html in a browser or text editor.
    """
    try:
        content = page.content()
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        console.print(f"[dim]DOM dumped to {output_path} ({len(content):,} bytes)[/dim]")
    except Exception as exc:
        console.print(f"[red]DOM dump failed: {exc}[/red]")


def _first_visible(page, selectors: list[str], timeout: int = ELEMENT_TIMEOUT):
    """
    Try each selector in order; return the first Locator that finds
    at least one visible element within *timeout* ms.
    Returns None if nothing found.
    """
    for sel in selectors:
        try:
            if sel.startswith("//"):
                loc = page.locator(f"xpath={sel}")
            else:
                loc = page.locator(sel)
            loc.first.wait_for(state="visible", timeout=timeout)
            count = loc.count()
            if count > 0:
                return loc
        except Exception:
            continue
    return None


def _get_ticket_number(ticket_element) -> str:
    """
    Extract the ticket number string from a ticket row element.

    CONFIRMED winway.lk format (2026-09-28):
      Card inner_text = "1\nO\nW\n18\n58\n68\n77\n\nAdd"
      - First token is the row index (1-5), skip it
      - Remaining tokens before 'Add' are the ticket components
      - Join them: e.g. "O W 18 58 68 77"
    """
    # Primary: parse the full card text
    try:
        raw = ticket_element.inner_text(timeout=3_000).strip()
        parts = [p.strip() for p in raw.split("\n") if p.strip()]
        # Remove trailing button text
        parts = [p for p in parts if p.lower() not in ("add", "added", "adding", "remove", "delete")]
        # Remove leading row index (single digit)
        if parts and parts[0].isdigit() and len(parts[0]) == 1:
            parts = parts[1:]
        if parts:
            return " ".join(parts)
    except Exception:
        pass

    # Fallback: try specific selectors
    for sel in TICKET_NUMBER_SELECTORS[:3]:
        try:
            inner = ticket_element.locator(sel)
            text = inner.first.inner_text(timeout=2_000).strip()
            if text:
                return text
        except Exception:
            continue

    return "UNKNOWN"


def _get_cart_ticket_number(cart_element) -> str:
    """Extract the ticket number from a cart row element."""
    for sel in TICKET_NUMBER_SELECTORS:
        try:
            if sel.startswith("//"):
                inner = cart_element.locator(f"xpath={sel}")
            else:
                inner = cart_element.locator(sel)
            text = inner.first.inner_text(timeout=3_000).strip()
            if text:
                return text
        except Exception:
            continue

    try:
        return cart_element.inner_text(timeout=3_000).split("\n")[0].strip()
    except Exception:
        return "UNKNOWN"


# ---------------------------------------------------------------------------
# Phase 1 – Ticket selection loop
# ---------------------------------------------------------------------------

def phase1_fill_cart(
    page,
    lottery_name: str,
    target: int = 10,
    max_seconds: int = 200,
) -> int:
    """
    Loop through ticket pages, randomly select 1 of the 5 available
    tickets on each page, add it to the cart.  Continue until *target*
    tickets are in the cart or time runs out.

    Returns the number of tickets successfully added.
    """
    added       = 0
    start_time  = time.time()
    page_num    = 1

    console.rule(f"[bold cyan]Phase 1 – Filling cart (target: {target} tickets)[/bold cyan]")

    # ── Auto-navigate to the lottery ticket page ──
    # winway.lk is a true SPA – the URL always stays at '/'.
    # Navigation is done by clicking 'Quick Buy' on the lottery card.
    # We detect if we're already on the ticket selection view by checking
    # whether any 'Add' buttons (from-primaryColor) are visible.
    from nlb_database import _name_to_slug  # type: ignore

    def _already_on_ticket_page() -> bool:
        """Return True if the ticket selection view is currently visible."""
        try:
            loc = page.locator("button.from-primaryColor")
            loc.first.wait_for(state="visible", timeout=3_000)
            return loc.count() > 0
        except Exception:
            return False

    if not _already_on_ticket_page():
        console.print(f"[cyan]Finding '{lottery_name}' lottery card and clicking Quick Buy…[/cyan]")

        # Strategy 1: find a card whose text contains the lottery name,
        # then click its 'Quick Buy' button.
        clicked = False
        try:
            # Get all lottery card containers that have a Quick Buy button
            cards = page.query_selector_all("div.flex.flex-col, article, section > div")
            for card in cards:
                try:
                    card_txt = (card.inner_text() or "").strip()
                    # Check if this card mentions our lottery (case-insensitive)
                    if lottery_name.lower() in card_txt.lower() or \
                       "mahajana" in card_txt.lower() and "sampatha" in card_txt.lower():
                        # Find Quick Buy button inside this card
                        qb = card.query_selector("button")
                        while qb:
                            txt = (qb.inner_text() or "").strip()
                            if "quick buy" in txt.lower() or "buy" in txt.lower():
                                qb.scroll_into_view_if_needed()
                                qb.click()
                                page.wait_for_timeout(SHORT_WAIT)
                                if _already_on_ticket_page():
                                    console.print(f"[green]Navigated via card click.[/green]")
                                    clicked = True
                                break
                            break
                    if clicked:
                        break
                except Exception:
                    continue
        except Exception as exc:
            console.print(f"[yellow]Card search error: {exc}[/yellow]")

        # Strategy 2: use Playwright's text locator to find Quick Buy
        # near the lottery name text
        if not clicked:
            try:
                console.print("[dim]Trying text-based Quick Buy search…[/dim]")
                # Find all Quick Buy buttons and check which one is near our lottery name
                qb_buttons = page.query_selector_all("button")
                for btn in qb_buttons:
                    txt = (btn.inner_text() or "").strip()
                    if "quick buy" in txt.lower() or "buy" in txt.lower():
                        # Check parent container for lottery name
                        parent_txt = ""
                        try:
                            parent = btn.evaluate_handle("e => e.closest('div[class*=\"flex\"]') || e.parentElement")
                            parent_el = parent.as_element()
                            if parent_el:
                                parent_txt = (parent_el.inner_text() or "").lower()
                        except Exception:
                            pass
                        if not parent_txt:
                            # Check 3 levels up
                            try:
                                ancestor = btn.evaluate("e => { let p=e; for(let i=0;i<5;i++){p=p.parentElement; if(!p) break;} return p ? p.innerText : ''; }")
                                parent_txt = ancestor.lower()
                            except Exception:
                                pass

                        if lottery_name.lower() in parent_txt or \
                           ("mahajana" in parent_txt and "sampatha" in parent_txt):
                            btn.scroll_into_view_if_needed()
                            btn.click()
                            page.wait_for_timeout(SHORT_WAIT)
                            if _already_on_ticket_page():
                                console.print(f"[green]Navigated via Quick Buy click.[/green]")
                                clicked = True
                                break
            except Exception as exc:
                console.print(f"[yellow]Quick Buy search error: {exc}[/yellow]")

        if not clicked:
            console.print(
                f"[red]Could not auto-navigate to '{lottery_name}' ticket page.[/red]\n"
                f"[yellow]Please manually navigate to the ticket selection page in Chrome, "
                f"then the agent will continue automatically.[/yellow]"
            )
            # Wait up to 30s for user to navigate manually
            for _ in range(30):
                page.wait_for_timeout(1_000)
                if _already_on_ticket_page():
                    console.print("[green]Ticket page detected — continuing.[/green]")
                    break
            else:
                console.print("[red]Timed out waiting for ticket page. Proceeding anyway.[/red]")
    else:
        console.print("[dim]Already on ticket selection page.[/dim]")

    while added < target:
        elapsed = time.time() - start_time
        if elapsed > max_seconds:
            console.print(
                f"[yellow]Time limit reached ({max_seconds}s). "
                f"Added {added}/{target} tickets.[/yellow]"
            )
            break


        console.print(
            f"\n[bold]Page {page_num}[/bold] | "
            f"Tickets in cart: [cyan]{added}[/cyan] | "
            f"Elapsed: [dim]{elapsed:.0f}s[/dim]"
        )

        # Wait for ticket rows to appear
        ticket_loc = _first_visible(page, TICKET_ROW_SELECTORS, timeout=PAGE_LOAD_TIMEOUT)
        if ticket_loc is None:
            console.print("[red]Could not find ticket rows on this page.[/red]")
            break

        count = ticket_loc.count()
        console.print(f"  Found [green]{count}[/green] ticket row(s).")

        if count == 0:
            console.print("[yellow]No tickets visible. Moving to next page.[/yellow]")
        else:
            # Pick one at random
            chosen_idx = random.randint(0, min(count, 5) - 1)
            chosen     = ticket_loc.nth(chosen_idx)

            ticket_no = _get_ticket_number(chosen)
            console.print(f"  → Selecting ticket [bold]{ticket_no}[/bold] (index {chosen_idx})")

            # Click Add to Cart inside the chosen row
            add_btn = None
            for sel in ADD_TO_CART_SELECTORS:
                try:
                    if sel.startswith("//"):
                        loc = chosen.locator(f"xpath={sel}")
                    else:
                        loc = chosen.locator(sel)
                    loc.first.wait_for(state="visible", timeout=5_000)
                    add_btn = loc.first
                    break
                except Exception:
                    continue

            if add_btn is None:
                # Last-ditch: click the row itself (some SPAs handle this)
                console.print(
                    "  [yellow]Add-to-cart button not found; clicking the row.[/yellow]"
                )
                try:
                    chosen.click(timeout=5_000)
                except Exception as exc:
                    console.print(f"  [red]Row click failed: {exc}[/red]")
            else:
                try:
                    add_btn.scroll_into_view_if_needed(timeout=5_000)
                    add_btn.click(timeout=5_000)
                    console.print(f"  [green]✓ Add-to-cart clicked.[/green]")
                except Exception as exc:
                    console.print(f"  [red]Add-to-cart click failed: {exc}[/red]")

            page.wait_for_timeout(CART_ACTION_WAIT)

            # Optimistic count – check the cart badge if available
            try:
                badge_loc = _first_visible(page, CART_COUNT_SELECTORS, timeout=3_000)
                if badge_loc:
                    badge_text = badge_loc.first.inner_text(timeout=2_000).strip()
                    numeric    = "".join(c for c in badge_text if c.isdigit())
                    if numeric:
                        added = int(numeric)
                        console.print(f"  Cart badge shows: [bold]{added}[/bold]")
                else:
                    added += 1
            except Exception:
                added += 1

        if added >= target:
            console.print(f"\n[green bold]✓ Target of {target} tickets reached![/green bold]")
            break

        # Navigate to next page
        next_loc = _first_visible(page, NEXT_PAGE_SELECTORS, timeout=8_000)
        if next_loc is None:
            console.print("[yellow]No 'Next' page link found. Refreshing current page.[/yellow]")
            try:
                page.reload(wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
            except Exception:
                pass
        else:
            try:
                next_loc.first.click(timeout=8_000)
                page.wait_for_load_state("domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
                console.print(f"  [dim]→ Navigated to page {page_num + 1}[/dim]")
            except Exception as exc:
                console.print(f"  [red]Next-page navigation failed: {exc}[/red]")
                break

        page_num += 1
        time.sleep(SHORT_WAIT / 1000)

    return added


# ---------------------------------------------------------------------------
# Phase 2 – Scrape cart & rank tickets
# ---------------------------------------------------------------------------

def phase2_scrape_cart(page) -> list[str]:
    """
    Navigate to the cart/checkout page and scrape all ticket numbers.
    Returns a list of ticket number strings.
    """
    console.rule("[bold cyan]Phase 2 – Scraping cart[/bold cyan]")
    console.print(f"Navigating to cart: [bold]{CART_URL}[/bold]")

    try:
        page.goto(CART_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
    except Exception as exc:
        console.print(f"[red]Failed to navigate to cart: {exc}[/red]")
        # Try the checkout URL as fallback
        try:
            page.goto(CHECKOUT_URL, wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT)
        except Exception:
            pass

    page.wait_for_timeout(SHORT_WAIT)

    cart_loc = _first_visible(page, CART_ITEM_SELECTORS, timeout=PAGE_LOAD_TIMEOUT)
    if cart_loc is None:
        console.print("[red]Could not locate cart items on the page.[/red]")
        console.print(
            "[dim]Tip: Open DevTools on the cart page and find the selector "
            "for cart rows, then update CART_ITEM_SELECTORS in this script.[/dim]"
        )
        return []

    count = cart_loc.count()
    console.print(f"Found [green]{count}[/green] item(s) in cart.")

    ticket_numbers: list[str] = []
    for i in range(count):
        item = cart_loc.nth(i)
        num  = _get_cart_ticket_number(item)
        ticket_numbers.append(num)
        console.print(f"  [{i + 1:02d}] {num}")

    return ticket_numbers


# ---------------------------------------------------------------------------
# Phase 3 – Score, rank, and remove low scorers
# ---------------------------------------------------------------------------

def phase3_rank_and_remove(
    page,
    ticket_numbers: list[str],
    lottery_name:   str,
    keep:           int = 2,
) -> list[str]:
    """
    Score all tickets in the cart, identify the bottom (len - keep) tickets,
    and remove them from the cart UI.

    Returns the list of kept ticket numbers.
    """
    console.rule("[bold cyan]Phase 3 – Scoring & removing low-scorers[/bold cyan]")

    if not ticket_numbers:
        console.print("[red]No tickets to score.[/red]")
        return []

    if not _PREDICTOR_AVAILABLE:
        console.print(
            "[red]predictor.py could not be imported. "
            "Skipping scoring step.[/red]"
        )
        return ticket_numbers[:keep]

    # ------------------------------------------------------------------
    # Score tickets
    # ------------------------------------------------------------------
    console.print(f"Scoring [bold]{len(ticket_numbers)}[/bold] tickets against "
                  f"[bold]{lottery_name}[/bold] history…")

    try:
        ranked = score_tickets(lottery_name, ticket_numbers)
    except Exception as exc:
        console.print(f"[red]Scoring failed: {exc}[/red]")
        console.print("[yellow]Keeping the first 2 tickets as fallback.[/yellow]")
        return ticket_numbers[:keep]

    # Display ranking table
    table = Table(title="Ticket Rankings", show_lines=True)
    table.add_column("Rank",   style="dim",    justify="center", width=6)
    table.add_column("Ticket", style="cyan",   justify="left")
    table.add_column("Score",  style="green",  justify="right")
    table.add_column("Action", style="yellow", justify="center")

    to_keep   = [t for t, _ in ranked[:keep]]
    to_remove = [t for t, _ in ranked[keep:]]

    for rank, (ticket, score) in enumerate(ranked, 1):
        action = "[green]KEEP[/green]" if ticket in to_keep else "[red]REMOVE[/red]"
        table.add_row(str(rank), ticket, f"{score:.2f}", action)

    console.print(table)
    console.print(
        f"\nKeeping: [green]{to_keep}[/green]\n"
        f"Removing: [red]{to_remove}[/red]"
    )

    # ------------------------------------------------------------------
    # Remove low-scoring tickets from the cart UI
    # ------------------------------------------------------------------
    if not to_remove:
        console.print("[green]Nothing to remove.[/green]")
        return to_keep

    console.print(f"\nRemoving [bold]{len(to_remove)}[/bold] ticket(s) from cart…")

    for target_ticket in to_remove:
        console.print(f"  Removing [red]{target_ticket}[/red]…")

        # Re-query cart items each iteration (DOM changes after remove)
        cart_loc = _first_visible(page, CART_ITEM_SELECTORS, timeout=10_000)
        if cart_loc is None:
            console.print("  [red]Cart items not found – aborting removes.[/red]")
            break

        item_count = cart_loc.count()
        removed    = False

        for i in range(item_count):
            try:
                item    = cart_loc.nth(i)
                num     = _get_cart_ticket_number(item)
                # Fuzzy match: check if the target is contained in the row text
                if target_ticket.upper() in num.upper() or num.upper() in target_ticket.upper():
                    # Find and click the remove button
                    remove_btn = None
                    for sel in REMOVE_BUTTON_SELECTORS:
                        try:
                            if sel.startswith("//"):
                                loc = item.locator(f"xpath={sel}")
                            else:
                                loc = item.locator(sel)
                            loc.first.wait_for(state="visible", timeout=3_000)
                            remove_btn = loc.first
                            break
                        except Exception:
                            continue

                    if remove_btn is None:
                        console.print(f"  [red]Remove button not found for {target_ticket}.[/red]")
                        break

                    remove_btn.scroll_into_view_if_needed(timeout=3_000)
                    remove_btn.click(timeout=5_000)
                    page.wait_for_timeout(REMOVE_ACTION_WAIT)
                    console.print(f"  [green]✓ Removed {target_ticket}.[/green]")
                    removed = True
                    break

            except Exception as exc:
                console.print(f"  [yellow]Error on item {i}: {exc}[/yellow]")
                continue

        if not removed:
            console.print(
                f"  [yellow]Could not find '{target_ticket}' in cart – may have already been removed.[/yellow]"
            )

    return to_keep


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def run_agent(
    cdp_port:       int    = 9222,
    lottery_name:   str    = "Mahajana Sampatha",
    target_tickets: int    = 10,
    keep:           int    = 2,
    max_seconds:    int    = 220,
) -> None:
    """
    Full end-to-end agent run.
    """
    from playwright.sync_api import sync_playwright

    cdp_url    = f"http://localhost:{cdp_port}"
    start_time = time.time()

    console.print(
        Panel.fit(
            f"[bold cyan]Winway Agent[/bold cyan]\n"
            f"CDP: {cdp_url} | Lottery: {lottery_name}\n"
            f"Target tickets: {target_tickets} | Keep: {keep} | Time budget: {max_seconds}s",
            title="⚡ Starting",
        )
    )

    pw = browser = page = None
    try:
        pw, browser, page = _connect_to_chrome(cdp_url)

        # ──────────────────────────────────────────────────────────────
        # Phase 1 – Fill the cart with random tickets
        # ──────────────────────────────────────────────────────────────
        time_left = max_seconds - int(time.time() - start_time)
        added = phase1_fill_cart(
            page,
            lottery_name=lottery_name,
            target=target_tickets,
            max_seconds=time_left,
        )

        if added == 0:
            console.print("[red bold]No tickets were added to cart. Aborting.[/red bold]")
            return

        # ──────────────────────────────────────────────────────────────
        # Phase 2 – Go to cart and scrape ticket numbers
        # ──────────────────────────────────────────────────────────────
        ticket_numbers = phase2_scrape_cart(page)

        if not ticket_numbers:
            console.print("[red bold]Could not read ticket numbers from cart. Aborting.[/red bold]")
            return

        # ──────────────────────────────────────────────────────────────
        # Phase 3 – Score and remove low-scorers
        # ──────────────────────────────────────────────────────────────
        kept = phase3_rank_and_remove(
            page,
            ticket_numbers,
            lottery_name,
            keep=keep,
        )

        # ──────────────────────────────────────────────────────────────
        # Summary & pause for manual payment
        # ──────────────────────────────────────────────────────────────
        elapsed = time.time() - start_time
        console.rule("[bold green]Agent Complete[/bold green]")
        console.print(
            Panel.fit(
                f"[green]✓ Done in {elapsed:.0f}s[/green]\n\n"
                f"[bold]Kept tickets ({len(kept)}):[/bold]\n"
                + "\n".join(f"  • {t}" for t in kept)
                + "\n\n[yellow]⏸ Script paused. Complete payment manually in the browser.[/yellow]\n"
                  "[dim]Press Enter here to exit the script when done.[/dim]",
                title="🎟 Results",
            )
        )

        try:
            input("\nPress Enter to exit…")
        except (EOFError, KeyboardInterrupt):
            pass

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
    except Exception as exc:
        console.print_exception(show_locals=False)
        console.print(f"\n[red bold]Fatal error: {exc}[/red bold]")
    finally:
        # We connected to an existing browser so we do NOT close it –
        # just disconnect the Playwright context.
        if pw:
            try:
                pw.stop()
            except Exception:
                pass
        console.print("[dim]Playwright disconnected.[/dim]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Winway.lk Playwright automation agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--cdp-port", type=int, default=9222,
        help="Chrome remote debugging port (default: 9222)",
    )
    parser.add_argument(
        "--lottery", default="Mahajana Sampatha",
        help='Lottery name to score against (default: "Mahajana Sampatha")',
    )
    parser.add_argument(
        "--target-tickets", type=int, default=10,
        help="Number of tickets to add to cart (default: 10)",
    )
    parser.add_argument(
        "--keep", type=int, default=2,
        help="Number of top-scoring tickets to keep (default: 2)",
    )
    parser.add_argument(
        "--time-budget", type=int, default=220,
        help="Maximum seconds for the ticket-filling phase (default: 220)",
    )

    args = parser.parse_args()
    run_agent(
        cdp_port       = args.cdp_port,
        lottery_name   = args.lottery,
        target_tickets = args.target_tickets,
        keep           = args.keep,
        max_seconds    = args.time_budget,
    )


if __name__ == "__main__":
    main()
