

await message.answer(  
    t(message.from_user.id, "send_link"),  
    reply_markup=get_main_keyboard(message.from_user.id),  
)

@dp.message_handler(lambda m: m.text in ["💡 Возможность", "💡 Opportunity"])
async def opportunity_handler(message: types.Message):
uid = message.from_user.id
lang = get_user_lang(uid)
await message.answer(t(uid, "searching_opportunity"))
try:
agent = OpportunityAgent()
result = agent.run(lang=lang)
if not result or result.get("opportunity_score", 0) == 0:
await message.answer(t(uid, "no_opportunities"), reply_markup=get_main_keyboard(uid))
return
text = _format_opportunity(result, uid)
await message.answer(text, reply_markup=get_main_keyboard(uid))
except Exception as e:
await message.answer(f"{t(uid, 'error')} {e}", reply_markup=get_main_keyboard(uid))

@dp.message_handler(lambda m: m.text in ["📊 История", "📊 History"])
async def history_handler(message: types.Message):
uid = message.from_user.id
records = get_recent_analyses(limit=5)
if not records:
await message.answer(t(uid, "no_history"), reply_markup=get_main_keyboard(uid))
return
lines = [t(uid, "recent")]
for r in records:
lines.append(f"• {_escape(r['question'][:60])}")
lines.append(f"  {t(uid, 'category')}: {r['category']} | {t(uid, 'confidence')}: {r['confidence']}")
lines.append(f"  {t(uid, 'system_probability')}: {r['system_probability']}")
lines.append("")
await message.answer("\n".join(lines), reply_markup=get_main_keyboard(uid))

@dp.message_handler(lambda m: m.text in ["🏆 Топ", "🏆 Top"])
async def top_handler(message: types.Message):
uid = message.from_user.id
records = get_top_opportunities(limit=5)
if not records:
await message.answer(t(uid, "no_opportunities"), reply_markup=get_main_keyboard(uid))
return
lines = [t(uid, "top")]
for r in records:
lines.append(f"• {_escape(r['question'][:60])}")
lines.append(f"  {t(uid, 'score')}: {r['opportunity_score']} | {t(uid, 'confidence')}: {r['confidence']}")
lines.append(f"  {t(uid, 'system_probability')}: {r['system_probability']}")
lines.append("")
await message.answer("\n".join(lines), reply_markup=get_main_keyboard(uid))

@dp.message_handler(lambda m: m.text and "polymarket.com" in m.text)
async def analyze_url_handler(message: types.Message):
uid = message.from_user.id
lang = get_user_lang(uid)
await message.answer(t(uid, "analyzing"))
try:
agent = ChiefAgent()
result = agent.run(message.text.strip(), lang=lang)
if not result:
await message.answer(t(uid, "no_answer"), reply_markup=get_main_keyboard(uid))
return
text = _format_analysis(result, uid)
await message.answer(text, reply_markup=get_main_keyboard(uid))
except Exception as e:
await message.answer(f"{t(uid, 'error')} {e}", reply_markup=get_main_keyboard(uid))

@dp.message_handler()
async def fallback_handler(message: types.Message):
await message.answer(
t(message.from_user.id, "fallback"),
reply_markup=get_main_keyboard(message.from_user.id),
)

def _format_analysis(result: dict, uid: int) -> str:
return (
f"🔍 DeepAlpha Analysis\n\n"
f"{t(uid, 'question')}: {_escape(result.get('question', ''))}\n"
f"{t(uid, 'category')}: {_escape(result.get('category', ''))}\n"
f"{t(uid, 'market_probability')}: {_escape(result.get('market_probability', ''))}\n"
f"{t(uid, 'system_probability')}: {_escape(result.get('probability', ''))}\n"
f"{t(uid, 'confidence')}: {_escape(result.get('confidence', ''))}\n\n"
f"{_escape(result.get('conclusion', ''))}"
)

def _format_opportunity(result: dict, uid: int) -> str:
return (
f"💡 DeepAlpha Opportunity\n\n"
f"{t(uid, 'question')}: {_escape(result.get('question', ''))}\n"
f"{t(uid, 'category')}: {_escape(result.get('category', ''))}\n"
f"{t(uid, 'market_probability')}: {_escape(result.get('market_probability', ''))}\n"
f"{t(uid, 'system_probability')}: {_escape(result.get('probability', ''))}\n"
f"{t(uid, 'confidence')}: {_escape(result.get('confidence', ''))}\n"
f"{t(uid, 'score')}: {result.get('opportunity_score', '')}\n\n"
f"{_escape(result.get('conclusion', ''))}"
)
