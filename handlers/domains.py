from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

import db
import shortio
from handlers.auth import restricted

_MENU_TEXTS = filters.Text(["🔗 New link", "☰ Menu"])

# ── States ─────────────────────────────────────────────────────────────────
(
    DOMAINS_MENU,
    ADD_API_KEY,
    PICK_SHORTIO_DOMAINS,
    REMOVE_PICK,
    EDIT_PICK,
    EDIT_NICKNAME,
    VIEW_LINKS_PICK,
    VIEW_LINKS,
) = range(8)

LINKS_PAGE_SIZE = 10


def _truncate(text: str, length: int) -> str:
    return text[:length] + ("…" if len(text) > length else "")


def _menu_keyboard(has_domains: bool = True):
    rows = []
    if has_domains:
        rows.append([InlineKeyboardButton("🔗 View links", callback_data="dom_viewlinks")])
    rows.append([InlineKeyboardButton("➕ Add domain", callback_data="dom_add")])
    if has_domains:
        rows.append([InlineKeyboardButton("✏️ Edit domain nickname", callback_data="dom_edit")])
        rows.append([InlineKeyboardButton("❌ Remove domain", callback_data="dom_remove")])
    rows.append([InlineKeyboardButton("↩ Back", callback_data="dom_mainmenu")])
    return InlineKeyboardMarkup(rows)


def _domain_select_keyboard(domains: list, selected: set) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            f"{'✅' if i in selected else '◻️'} {d['hostname']}",
            callback_data=f"sdom:toggle:{i}",
        )]
        for i, d in enumerate(domains)
    ]
    rows.append([
        InlineKeyboardButton("✓ Done", callback_data="sdom:done"),
        InlineKeyboardButton("❌ Cancel", callback_data="sdom:cancel"),
    ])
    return InlineKeyboardMarkup(rows)


@restricted
async def domains_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if cq := update.callback_query:
        try:
            await cq.answer()
        except Exception:
            pass
    domains = db.list_domains()
    if domains:
        lines = [f"• {d['nickname']} — `{d['hostname']}`" if d['nickname'] != d['hostname'] else f"• `{d['hostname']}`" for d in domains]
        text = "Your domains:\n\n" + "\n".join(lines)
    else:
        text = "No domains saved yet."
    if cq:
        await cq.edit_message_text(text, reply_markup=_menu_keyboard(bool(domains)), parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=_menu_keyboard(bool(domains)), parse_mode="Markdown")
    return DOMAINS_MENU


async def domains_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "dom_mainmenu":
        from handlers.welcome import build_menu_keyboard
        await query.answer()
        await query.edit_message_text("Select an option below:", reply_markup=build_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END

    if query.data == "dom_add":
        await query.edit_message_text(
            "Paste your Short.io *private API key*.\n\n"
            "Get it from: https://app.short.io/settings/integrations/api-key\n\n"
            "⚠️ Use the *Private key*, not the Public key — the bot needs it to list your domains.",
            parse_mode="Markdown",
        )
        return ADD_API_KEY

    if query.data == "dom_viewlinks":
        domains = db.list_domains()
        if len(domains) == 1:
            # Skip domain picker if only one domain
            return await _sync_and_show_links(query, domains[0])
        rows = [
            [InlineKeyboardButton(d['nickname'], callback_data=f"vl_dom:{d['id']}")]
            for d in domains
        ]
        rows.append([InlineKeyboardButton("↩ Back", callback_data="vl_dom:back")])
        await query.edit_message_text("Which domain?", reply_markup=InlineKeyboardMarkup(rows))
        return VIEW_LINKS_PICK

    if query.data == "dom_edit":
        domains = db.list_domains()
        if not domains:
            await query.edit_message_text("No domains to edit.", reply_markup=_menu_keyboard())
            return DOMAINS_MENU
        rows = [
            [InlineKeyboardButton(d['nickname'], callback_data=f"editdom:{d['id']}")]
            for d in domains
        ]
        rows.append([InlineKeyboardButton("↩ Back", callback_data="editdom:back")])
        await query.edit_message_text("Which domain to rename?", reply_markup=InlineKeyboardMarkup(rows))
        return EDIT_PICK

    if query.data == "dom_remove":
        domains = db.list_domains()
        if not domains:
            await query.edit_message_text("No domains to remove.", reply_markup=_menu_keyboard(False))
            return DOMAINS_MENU
        rows = [
            [InlineKeyboardButton(d['nickname'], callback_data=f"rmdom:{d['id']}")]
            for d in domains
        ]
        rows.append([InlineKeyboardButton("↩ Back", callback_data="rmdom:back")])
        await query.edit_message_text("Which domain to remove?", reply_markup=InlineKeyboardMarkup(rows))
        return REMOVE_PICK

    return DOMAINS_MENU


async def add_api_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    api_key = update.message.text.strip()
    await update.message.reply_text("Checking key…")

    try:
        raw_domains = await shortio.fetch_domains(api_key)
    except Exception as e:
        await update.message.reply_text(
            f"❌ Could not connect to Short.io:\n`{e}`\n\nTry again or /cancel.",
            parse_mode="Markdown",
        )
        return ADD_API_KEY

    if not raw_domains:
        await update.message.reply_text("No domains found on this account. /cancel to exit.")
        return ADD_API_KEY

    context.user_data["new_api_key"] = api_key
    context.user_data["shortio_domains"] = raw_domains
    context.user_data["selected_domains"] = set()

    await update.message.reply_text(
        "Select the domains you want to add:",
        reply_markup=_domain_select_keyboard(raw_domains, set()),
    )
    return PICK_SHORTIO_DOMAINS


async def pick_shortio_domains(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "sdom:cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    domains = context.user_data["shortio_domains"]
    selected: set = context.user_data["selected_domains"]

    if query.data.startswith("sdom:toggle:"):
        idx = int(query.data.split(":")[2])
        if idx in selected:
            selected.discard(idx)
        else:
            selected.add(idx)
        await query.edit_message_text(
            "Select the domains you want to add:",
            reply_markup=_domain_select_keyboard(domains, selected),
        )
        return PICK_SHORTIO_DOMAINS

    if query.data == "sdom:done":
        if not selected:
            await query.answer("Select at least one domain.", show_alert=True)
            return PICK_SHORTIO_DOMAINS

        api_key = context.user_data["new_api_key"]
        added, errors = [], []
        for idx in sorted(selected):
            d = domains[idx]
            try:
                db.add_domain(
                    nickname=d["hostname"],
                    hostname=d["hostname"],
                    shortio_domain_id=d["id"],
                    api_key=api_key,
                )
                added.append(d["hostname"])
            except Exception as e:
                errors.append(f"{d['hostname']}: {e}")

        lines = []
        if added:
            lines.append("✅ Added: " + ", ".join(f"`{h}`" for h in added))
        if errors:
            lines.append("❌ Errors:\n" + "\n".join(errors))

        await query.edit_message_text("\n\n".join(lines), parse_mode="Markdown")
        return ConversationHandler.END

    return PICK_SHORTIO_DOMAINS


async def edit_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "editdom:back":
        domains = db.list_domains()
        if domains:
            lines = [f"• {d['nickname']} — `{d['hostname']}`" if d['nickname'] != d['hostname'] else f"• `{d['hostname']}`" for d in domains]
            text = "Your domains:\n\n" + "\n".join(lines)
        else:
            text = "No domains saved yet."
        await query.edit_message_text(text, reply_markup=_menu_keyboard(bool(domains)), parse_mode="Markdown")
        return DOMAINS_MENU

    domain_id = int(query.data.split(":")[1])
    domain = db.get_domain(domain_id)
    if not domain:
        await query.edit_message_text("Domain not found.")
        return ConversationHandler.END

    context.user_data["editing_domain_id"] = domain_id
    await query.edit_message_text(
        f"Current nickname: *{domain['nickname']}*\n\nType the new nickname:",
        parse_mode="Markdown",
    )
    return EDIT_NICKNAME


async def edit_nickname(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    nickname = update.message.text.strip()
    domain_id = context.user_data["editing_domain_id"]
    db.update_domain_nickname(domain_id, nickname)
    await update.message.reply_text(f"✅ Nickname updated to *{nickname}*.", parse_mode="Markdown")
    return ConversationHandler.END


async def remove_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "rmdom:back":
        domains = db.list_domains()
        lines = [f"• {d['nickname']} — `{d['hostname']}`" if d['nickname'] != d['hostname'] else f"• `{d['hostname']}`" for d in domains] if domains else ["No domains saved yet."]
        text = "\n".join(lines)
        await query.edit_message_text(text, reply_markup=_menu_keyboard(bool(domains)), parse_mode="Markdown")
        return DOMAINS_MENU

    domain_id = int(query.data.split(":")[1])
    domain = db.get_domain(domain_id)
    if domain:
        db.remove_domain(domain_id)
        await query.edit_message_text(f"✅ Removed *{domain['nickname']}*.", parse_mode="Markdown")
    else:
        await query.edit_message_text("Domain not found.")
    return ConversationHandler.END


async def _sync_and_show_links(query, domain: dict) -> int:
    await query.edit_message_text(f"Syncing links for <b>{domain['nickname']}</b>…", parse_mode="HTML")
    try:
        links = await shortio.fetch_links(domain["api_key"], domain["shortio_domain_id"])
        db.sync_links(domain["id"], links)
    except Exception:
        pass
    return await _show_links_page(query, domain, 0)


async def _show_links_page(query, domain: dict, page: int) -> int:
    links = db.list_links_for_domain(domain["id"])
    total = len(links)
    if not links:
        await query.edit_message_text(
            f"No links found for <b>{domain['nickname']}</b>.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    start = page * LINKS_PAGE_SIZE
    chunk = links[start : start + LINKS_PAGE_SIZE]

    lines = []
    for link in chunk:
        raw_title = link["title"] or link["slug"]
        title = _truncate(raw_title, 40)
        lines.append(f'• <code>{link["slug"]}</code> — <a href="{link["original_url"]}">{title}</a>')

    header = f"<b>{domain['nickname']}</b> — links {start + 1}–{min(start + LINKS_PAGE_SIZE, total)} of {total}:\n\n"
    text = header + "\n".join(lines)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("← Prev", callback_data=f"vl:{domain['id']}:{page - 1}"))
    if start + LINKS_PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Next →", callback_data=f"vl:{domain['id']}:{page + 1}"))
    rows = [nav] if nav else []
    rows.append([InlineKeyboardButton("↩ Back", callback_data="vl:back")])

    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(rows), parse_mode="HTML")
    return VIEW_LINKS


async def view_links_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "vl_dom:back":
        domains = db.list_domains()
        if domains:
            lines = [f"• {d['nickname']} — `{d['hostname']}`" if d['nickname'] != d['hostname'] else f"• `{d['hostname']}`" for d in domains]
            text = "Your domains:\n\n" + "\n".join(lines)
        else:
            text = "No domains saved yet."
        await query.edit_message_text(text, reply_markup=_menu_keyboard(bool(domains)), parse_mode="Markdown")
        return DOMAINS_MENU

    domain_id = int(query.data.split(":")[1])
    domain = db.get_domain(domain_id)
    if not domain:
        await query.edit_message_text("Domain not found.")
        return ConversationHandler.END
    return await _sync_and_show_links(query, domain)


async def view_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "vl:back":
        domains = db.list_domains()
        if domains:
            lines = [f"• {d['nickname']} — `{d['hostname']}`" if d['nickname'] != d['hostname'] else f"• `{d['hostname']}`" for d in domains]
            text = "Your domains:\n\n" + "\n".join(lines)
        else:
            text = "No domains saved yet."
        await query.edit_message_text(text, reply_markup=_menu_keyboard(bool(domains)), parse_mode="Markdown")
        return DOMAINS_MENU

    _, domain_id, page = query.data.split(":")
    domain = db.get_domain(int(domain_id))
    return await _show_links_page(query, domain, int(page))


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def domains_handler() -> ConversationHandler:
    from handlers.welcome import cancel_to_menu
    return ConversationHandler(
        entry_points=[
            CommandHandler("domains", domains_entry),
            CallbackQueryHandler(domains_entry, pattern="^menu:domains$"),
        ],
        states={
            DOMAINS_MENU: [CallbackQueryHandler(domains_menu, pattern="^dom_(add|edit|remove|mainmenu|viewlinks)$")],
            ADD_API_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~_MENU_TEXTS, add_api_key)],
            PICK_SHORTIO_DOMAINS: [CallbackQueryHandler(pick_shortio_domains, pattern="^sdom:")],
            REMOVE_PICK: [CallbackQueryHandler(remove_pick, pattern="^rmdom:")],
            EDIT_PICK: [CallbackQueryHandler(edit_pick, pattern="^editdom:")],
            EDIT_NICKNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~_MENU_TEXTS, edit_nickname)],
            VIEW_LINKS_PICK: [CallbackQueryHandler(view_links_pick, pattern="^vl_dom:")],
            VIEW_LINKS: [CallbackQueryHandler(view_links, pattern="^vl:")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Text(["☰ Menu"]), cancel_to_menu),
        ],
        per_message=False,
    )
