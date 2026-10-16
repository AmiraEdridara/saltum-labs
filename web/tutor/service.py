"""One tutor question, end to end: limits, routing, the tool loop, the number check, saving and cost logging.

The Claude API key is ANTHROPIC_API_KEY in .env, loaded into the environment like the other secrets (see
web/auth.py). The SDK reads it from there; this code never reads, prints, logs or returns it.
"""
import datetime as dt
import json
import os
import re

from .. import settings
from . import numbers, prompt
from .tools import SIMPLE_TOOLS, TOOLS, ToolRunner

COULD_NOT_VERIFY = "I couldn't verify the numbers in that answer, please rephrase it."
DEFINITION_Q = re.compile(r"^\s*(what('s| is| are| does)|define|definition of|meaning of|explain (the term|what))\b", re.I)
NEEDS_DATA = re.compile(r"\d|\bthis\b|\bthese\b|\btoday\b|\bcpi\b|\bfomc\b|\bpayrolls?\b|\bnfp\b|\bevent\b|\bperiod\b|"
                        r"\bheld\b|\bsurviv|\balways\b|\bnever\b|\bshould\b|\bbuy\b|\bsell\b|\btrade\b|\bwhy\b", re.I)


class TutorError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def configured():
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def route(question):
    """'simple' (Haiku, definitions only) or 'analysis' (Sonnet, all tools): a rule, no extra model call."""
    q = question.strip()
    return "simple" if DEFINITION_Q.match(q) and not NEEDS_DATA.search(q) and len(q) <= 120 else "analysis"


def today():
    return dt.datetime.now(dt.timezone.utc)


def allowance(user):
    """Questions left under the plan's limit (UTC day for Student, UTC month for Tutor plus)."""
    now = today()
    if user["plan"] == "tutor_plus":
        used = user["tutor_msgs_month"] if user["tutor_month"] == now.strftime("%Y-%m") else 0
        return dict(limit=settings.TUTOR_MONTHLY_PLUS, used=used, left=max(0, settings.TUTOR_MONTHLY_PLUS - used), per="month")
    used = user["tutor_msgs_today"] if user["tutor_day"] == now.date().isoformat() else 0
    return dict(limit=settings.TUTOR_DAILY_FREE, used=used, left=max(0, settings.TUTOR_DAILY_FREE - used), per="day")


def cost(usage):
    total = 0.0
    for model, u in usage.items():
        p = settings.TUTOR_PRICES[model]
        total += (u["input"] * p["input"] + u["cache_write"] * p["cache_write"] + u["cache_read"] * p["cache_read"]
                  + u["output"] * p["output"]) / 1e6
    return total


class Tutor:
    def __init__(self, client, tools: ToolRunner):
        self.client, self.tools = client, tools
        self.usage, self.calls = {}, []
        self.checks = []                       # (model, draft, untraced numbers) for every answer checked

    def _add_usage(self, model, u):
        a = self.usage.setdefault(model, dict(input=0, output=0, cache_write=0, cache_read=0))
        a["input"] += u.input_tokens or 0
        a["output"] += u.output_tokens or 0
        a["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        a["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0

    async def _loop(self, model, system, tools, messages):
        """Messages API tool loop. Returns the final text; tool calls and results are kept in self.calls."""
        cached_tools = [dict(t) for t in tools]
        cached_tools[-1] = dict(cached_tools[-1], cache_control={"type": "ephemeral"})
        for _ in range(settings.TUTOR_MAX_TOOL_ROUNDS + 1):
            r = await self.client.messages.create(
                model=model, max_tokens=settings.TUTOR_MAX_TOKENS, tools=cached_tools, messages=messages,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}])
            self._add_usage(model, r.usage)
            messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in r.content]})
            uses = [b for b in r.content if b.type == "tool_use"]
            if r.stop_reason != "tool_use" or not uses:
                return "".join(b.text for b in r.content if b.type == "text").strip()
            results = []
            for b in uses:
                out = await self.tools.run(b.name, b.input)
                self.calls.append({"tool": b.name, "input": b.input, "result": out})
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(out)})
            messages.append({"role": "user", "content": results})
        return ""

    async def answer(self, question, ctx, history, earlier_tool_results):
        """Returns (text or None if unverified, route, regenerated)."""
        user_turn = f"{prompt.context_block(ctx)}\n\n{question}"
        base = []
        for q, a in history[-settings.TUTOR_HISTORY_TURNS:]:
            base += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
        r = route(question)
        if r == "simple":
            text = await self._loop(settings.TUTOR_MODEL_SIMPLE, prompt.SIMPLE, SIMPLE_TOOLS,
                                    base + [{"role": "user", "content": user_turn}])
            if text.strip() != "NEED_DATA" and text:
                bad = numbers.unverified(text, self._sources(question, ctx, earlier_tool_results))
                self.checks.append((settings.TUTOR_MODEL_SIMPLE, text, bad))
                if not bad:
                    return text, "simple", False
            r = "simple->analysis"            # needs data, or failed the check: the stronger model answers instead
        messages = base + [{"role": "user", "content": user_turn}]
        text = await self._loop(settings.TUTOR_MODEL_ANALYSIS, prompt.ANALYSIS, TOOLS, messages)
        bad = numbers.unverified(text, self._sources(question, ctx, earlier_tool_results))
        self.checks.append((settings.TUTOR_MODEL_ANALYSIS, text, bad))
        if not bad:
            return text, r, False
        # One regeneration, telling the model which numbers could not be traced.
        messages.append({"role": "user", "content": (
            "Automatic check: these numbers or dates in your answer are not in any tool result: "
            f"{', '.join(dict.fromkeys(bad))}. Numbers you worked out yourself (differences, complements, examples) "
            "count as not in a tool result. Call the tools you need, or leave those numbers out. Then write the whole "
            "answer again for the user, from the start, using only numbers that appear in tool results, with their "
            "sources. Do not mention this check, the removed numbers or your earlier answer.")})
        text = await self._loop(settings.TUTOR_MODEL_ANALYSIS, prompt.ANALYSIS, TOOLS, messages)
        bad = numbers.unverified(text, self._sources(question, ctx, earlier_tool_results))
        self.checks.append((settings.TUTOR_MODEL_ANALYSIS, text, bad))
        return (None if bad or not text else text), r, True

    def _sources(self, question, ctx, earlier):
        return [question, ctx or {}, *earlier, *[c["result"] for c in self.calls]]


async def ask(*, client, tools, store, users, user, question, ctx_raw, session_id=None):
    """The whole request. Raises TutorError for the client; logs usage for every call made."""
    question = (question or "").strip()
    if not question:
        raise TutorError(400, "Type a question.")
    if len(question) > settings.TUTOR_MAX_QUESTION:
        raise TutorError(400, f"Questions are limited to {settings.TUTOR_MAX_QUESTION} characters.")
    if not configured():
        raise TutorError(503, "The tutor is not configured on this server (ANTHROPIC_API_KEY is missing from .env).")
    left = allowance(user)
    if left["left"] <= 0:
        raise TutorError(429, f"You have used your {left['limit']} tutor questions this {left['per']}. "
                              + ("They reset at midnight UTC." if left["per"] == "day" else "They reset at the start of next month (UTC)."))
    save = user["plan"] in settings.TUTOR_SAVE_SESSIONS_PLANS
    history, earlier = [], []
    if session_id is not None:
        if not save or not store.session(user["id"], session_id):
            raise TutorError(404, "That tutor session was not found.")
        msgs = store.messages(session_id)
        pairs = list(zip(msgs[0::2], msgs[1::2]))
        history = [(q["text"], a["text"]) for q, a in pairs]
        earlier = [c["result"] for _, a in pairs for c in a["tools"]]
    ctx = prompt.clean_context(ctx_raw)
    t = Tutor(client, tools)
    status, text, r, regen = "error", None, route(question), False
    try:
        text, r, regen = await t.answer(question, ctx, history, earlier)
        status = "answered" if text else "unverified"
    except TutorError:
        raise
    except Exception as e:                       # API or network failure: not counted, no details to the browser
        raise TutorError(502, f"The tutor is not available right now ({type(e).__name__}). Try again in a minute.") from None
    finally:
        store.log_usage(user_id=user["id"], plan=user["plan"], route=r, status=status, counted=int(status == "answered"),
                        tool_calls=len(t.calls), regenerated=int(regen), usage=t.usage, cost_usd=round(cost(t.usage), 6),
                        untraced=json.dumps(sorted({n for _, _, bad in t.checks for n in bad})))   # numbers only, no text
    if status == "answered":
        users.count_tutor_message(user["id"])
        if save:
            if session_id is None:
                session_id = store.new_session(user["id"], question)
            store.add_exchange(session_id, question, text, t.calls)
    shown = text if status == "answered" else COULD_NOT_VERIFY
    return dict(answer=shown, status=status, route=r, session_id=session_id if save else None, saved=save,
                sources=sorted({s for c in t.calls for s in (c["result"].get("source") or [])}),
                allowance=allowance(users.get(user["id"])))
