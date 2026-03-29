import re
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
import apple
from handlers.auth import restricted

_MENU_TEXTS = filters.Text(["🔗 New link", "☰ Menu"])

# ── States ─────────────────────────────────────────────────────────────────
(
    PODCASTS_MENU,
    ADD_APPLE_ID,
    ADD_DOMAIN_PICK,
    EDIT_PICK,
    EDIT_ACTIONS,
    EDIT_NAME,
    EDIT_DOMAIN_PICK,
) = range(7)


def _menu_keyboard(has_podcasts: bool = True):
    rows = [[InlineKeyboardButton("➕ Add podcast", callback_data="pod_add")]]
    if has_podcasts:
        rows.append([InlineKeyboardButton("✏️ Edit podcast", callback_data="pod_edit")])
    rows.append([InlineKeyboardButton("↩ Back", callback_data="pod_mainmenu")])
    return InlineKeyboardMarkup(rows)


@restricted
async def podcasts_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if cq := update.callback_query:
        try:
            await cq.answer()
        except Exception:
            pass
    podcasts = db.list_podcasts()
    if podcasts:
        lines = [
            f'• <a href="https://pod.link/{p["apple_id"]}">{p["name"]}</a> — <code>{p["hostname"]}</code>'
            for p in podcasts
        ]
        text = "Your podcasts:\n\n" + "\n".join(lines)
    else:
        text = "No podcasts saved yet."
    if cq:
        await cq.edit_message_text(text, reply_markup=_menu_keyboard(bool(podcasts)), parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=_menu_keyboard(bool(podcasts)), parse_mode="HTML")
    return PODCASTS_MENU


async def podcasts_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "pod_mainmenu":
        from handlers.welcome import build_menu_keyboard
        await query.answer()
        await query.edit_message_text("Select an option below:", reply_markup=build_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END

    if query.data == "pod_add":
        domains = db.list_domains()
        if not domains:
            await query.edit_message_text(
                "No domains saved yet. Add one with /domains first."
            )
            return ConversationHandler.END
        await query.edit_message_text(
            "Paste the Apple Podcasts URL:\n\n_(e.g. `https://podcasts.apple.com/us/podcast/my-show/id1669984779`)_",
            parse_mode="Markdown",
        )
        return ADD_APPLE_ID

    if query.data == "pod_edit":
        podcasts = db.list_podcasts()
        if not podcasts:
            await query.edit_message_text("No podcasts to edit.", reply_markup=_menu_keyboard(False))
            return PODCASTS_MENU
        rows = [
            [InlineKeyboardButton(p["name"], callback_data=f"editpod:{p['id']}")]
            for p in podcasts
        ]
        rows.append([InlineKeyboardButton("↩ Back", callback_data="editpod:back")])
        await query.edit_message_text("Which podcast?", reply_markup=InlineKeyboardMarkup(rows))
        return EDIT_PICK

    return PODCASTS_MENU


async def add_apple_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    match = re.search(r"id(\d+)", text)
    if not match:
        await update.message.reply_text(
            "Couldn't find a podcast ID in that URL. It should contain something like `id1669984779`. Try again:",
            parse_mode="Markdown",
        )
        return ADD_APPLE_ID
    apple_id = match.group(1)

    await update.message.reply_text("Looking up podcast…")

    try:
        result = await apple.lookup_podcast(apple_id)
    except Exception as e:
        await update.message.reply_text(
            f"❌ iTunes lookup failed:\n`{e}`\n\nTry again or /cancel.",
            parse_mode="Markdown",
        )
        return ADD_APPLE_ID

    if result is None:
        await update.message.reply_text("No podcast found with that ID. Double-check and try again:")
        return ADD_APPLE_ID

    feed_url = result.get("feedUrl")
    if not feed_url:
        await update.message.reply_text(
            "Podcast found but has no RSS feed URL. Try again or /cancel."
        )
        return ADD_APPLE_ID

    context.user_data["new_pod_apple_id"] = apple_id
    context.user_data["new_pod_rss"] = feed_url
    context.user_data["new_pod_name"] = result.get("trackName", apple_id)

    domains = db.list_domains()
    rows = [
        [InlineKeyboardButton(d['nickname'], callback_data=f"poddom:{d['id']}")]
        for d in domains
    ]
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="poddom:cancel")])
    await update.message.reply_text(
        f"Found: *{result.get('trackName', apple_id)}*\n\nWhich Short.io domain should be used for this podcast?",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown",
    )
    return ADD_DOMAIN_PICK


async def add_domain_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "poddom:cancel":
        await query.edit_message_text("Cancelled.")
        return ConversationHandler.END

    domain_id = int(query.data.split(":")[1])
    try:
        db.add_podcast(
            name=context.user_data["new_pod_name"],
            apple_id=context.user_data["new_pod_apple_id"],
            rss_url=context.user_data["new_pod_rss"],
            domain_id=domain_id,
        )
    except Exception as e:
        await query.edit_message_text(f"❌ Error saving podcast: `{e}`", parse_mode="Markdown")
        return ConversationHandler.END

    domain = db.get_domain(domain_id)
    await query.edit_message_text(
        f"✅ Added *{context.user_data['new_pod_name']}* using `{domain['hostname']}`.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def edit_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "editpod:back":
        podcasts = db.list_podcasts()
        if podcasts:
            lines = [
                f'• <a href="https://pod.link/{p["apple_id"]}">{p["name"]}</a> — <code>{p["hostname"]}</code>'
                for p in podcasts
            ]
            text = "Your podcasts:\n\n" + "\n".join(lines)
        else:
            text = "No podcasts saved yet."
        await query.edit_message_text(text, reply_markup=_menu_keyboard(bool(podcasts)), parse_mode="HTML")
        return PODCASTS_MENU

    podcast_id = int(query.data.split(":")[1])
    podcast = db.get_podcast(podcast_id)
    if not podcast:
        await query.edit_message_text("Podcast not found.")
        return ConversationHandler.END

    context.user_data["editing_podcast_id"] = podcast_id
    rows = [
        [InlineKeyboardButton("✏️ Edit nickname", callback_data=f"editpodact:rename:{podcast_id}")],
        [InlineKeyboardButton("🌐 Edit linked domain", callback_data=f"editpodact:domain:{podcast_id}")],
        [InlineKeyboardButton("❌ Remove podcast", callback_data=f"editpodact:remove:{podcast_id}")],
        [InlineKeyboardButton("↩ Back", callback_data="editpodact:back")],
    ]
    await query.edit_message_text(
        f"*{podcast['name']}*",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode="Markdown",
    )
    return EDIT_ACTIONS


async def edit_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "editpodact:back":
        podcasts = db.list_podcasts()
        rows = [
            [InlineKeyboardButton(p["name"], callback_data=f"editpod:{p['id']}")]
            for p in podcasts
        ]
        rows.append([InlineKeyboardButton("↩ Back", callback_data="editpod:back")])
        await query.edit_message_text("Which podcast?", reply_markup=InlineKeyboardMarkup(rows))
        return EDIT_PICK

    _, action, podcast_id = query.data.split(":")
    podcast_id = int(podcast_id)
    podcast = db.get_podcast(podcast_id)
    if not podcast:
        await query.edit_message_text("Podcast not found.")
        return ConversationHandler.END

    if action == "rename":
        context.user_data["editing_podcast_id"] = podcast_id
        await query.edit_message_text(
            f"Current nickname: *{podcast['name']}*\n\nType the new nickname:",
            parse_mode="Markdown",
        )
        return EDIT_NAME

    if action == "domain":
        context.user_data["editing_podcast_id"] = podcast_id
        domains = db.list_domains()
        rows = [
            [InlineKeyboardButton(d['nickname'], callback_data=f"editpoddom:{d['id']}")]
            for d in domains
        ]
        rows.append([InlineKeyboardButton("↩ Back", callback_data="editpoddom:back")])
        await query.edit_message_text(
            f"Pick the domain for *{podcast['name']}*:",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="Markdown",
        )
        return EDIT_DOMAIN_PICK

    if action == "remove":
        db.remove_podcast(podcast_id)
        await query.edit_message_text(f"✅ Removed *{podcast['name']}*.", parse_mode="Markdown")
        return ConversationHandler.END

    return EDIT_ACTIONS


async def edit_domain_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "editpoddom:back":
        podcast_id = context.user_data["editing_podcast_id"]
        podcast = db.get_podcast(podcast_id)
        rows = [
            [InlineKeyboardButton("✏️ Edit nickname", callback_data=f"editpodact:rename:{podcast_id}")],
            [InlineKeyboardButton("🌐 Edit linked domain", callback_data=f"editpodact:domain:{podcast_id}")],
            [InlineKeyboardButton("❌ Remove podcast", callback_data=f"editpodact:remove:{podcast_id}")],
            [InlineKeyboardButton("↩ Back", callback_data="editpodact:back")],
        ]
        await query.edit_message_text(
            f"*{podcast['name']}*",
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode="Markdown",
        )
        return EDIT_ACTIONS

    domain_id = int(query.data.split(":")[1])
    podcast_id = context.user_data["editing_podcast_id"]
    db.update_podcast_domain(podcast_id, domain_id)
    domain = db.get_domain(domain_id)
    podcast = db.get_podcast(podcast_id)
    await query.edit_message_text(
        f"✅ *{podcast['name']}* now linked to `{domain['hostname']}`.",
        parse_mode="Markdown",
    )
    return ConversationHandler.END


async def edit_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    podcast_id = context.user_data["editing_podcast_id"]
    db.update_podcast_name(podcast_id, name)
    await update.message.reply_text(f"✅ Podcast nickname updated to *{name}*.", parse_mode="Markdown")
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def podcasts_handler() -> ConversationHandler:
    from handlers.welcome import cancel_to_menu
    return ConversationHandler(
        entry_points=[
            CommandHandler("podcasts", podcasts_entry),
            CallbackQueryHandler(podcasts_entry, pattern="^menu:podcasts$"),
        ],
        states={
            PODCASTS_MENU: [CallbackQueryHandler(podcasts_menu, pattern="^pod_(add|edit|mainmenu)$")],
            ADD_APPLE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~_MENU_TEXTS, add_apple_id)],
            ADD_DOMAIN_PICK: [CallbackQueryHandler(add_domain_pick, pattern="^poddom:")],
            EDIT_PICK: [CallbackQueryHandler(edit_pick, pattern="^editpod:")],
            EDIT_ACTIONS: [CallbackQueryHandler(edit_actions, pattern="^editpodact:")],
            EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~_MENU_TEXTS, edit_name)],
            EDIT_DOMAIN_PICK: [CallbackQueryHandler(edit_domain_pick, pattern="^editpoddom:")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            MessageHandler(filters.Text(["☰ Menu"]), cancel_to_menu),
        ],
        per_message=False,
    )
