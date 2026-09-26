"""FakeProvider: deterministic and offline, for tests and for running the AI flow with no key
(LLM_PROVIDER=fake). Same interface and the same validation as a real provider."""

from __future__ import annotations

from core.contracts import Lang
from core.suggest.provider import Options, Sentences, SuggestContext, drop_known

# {topic} is the last label of the path, lower case.
LEAF: dict[Lang, list[str]] = {
    "en": [
        "{Topic}. Can you help me, please?",
        "It's about {topic}. Please come here.",
        "{Topic}, please. Thank you.",
        "I want to tell you about {topic}.",
        "{Topic}. Not urgent, when you can.",
        "{Topic}. Please, as soon as you can.",
    ],
    "es": [
        "{Topic}. ¿Me ayudas, por favor?",
        "Es sobre {topic}. Ven, por favor.",
        "{Topic}, por favor. Gracias.",
        "Quiero decirte algo de {topic}.",
        "{Topic}. No es urgente, cuando puedas.",
        "{Topic}. Por favor, lo antes posible.",
    ],
}

NOW: dict[Lang, list[str]] = {
    "en": [
        "Good {part}. How are you?",
        "I'd like some water, please.",
        "Thank you for being here.",
        "Can you sit with me for a while?",
        "I'm comfortable, thank you.",
        "Can you turn on some music?",
    ],
    "es": [
        "Buenas {part}. ¿Cómo estás?",
        "Quiero agua, por favor.",
        "Gracias por estar aquí.",
        "¿Te sientas conmigo un rato?",
        "Estoy cómodo, gracias.",
        "¿Me pones algo de música?",
    ],
}

PARTS: dict[Lang, tuple[str, str, str]] = {
    "en": ("morning", "afternoon", "evening"),
    "es": ("días", "tardes", "noches"),
}

OPTIONS: dict[Lang, list[tuple[str, str]]] = {
    "en": [
        ("Yes", "Yes."),
        ("No", "No."),
        ("Wait", "Please wait a moment."),
        ("Thank you", "Thank you."),
        ("Later", "Let's do it later."),
        ("Not sure", "I'm not sure."),
        ("Again", "Can you do that again?"),
        ("Stop", "Please stop."),
        ("Help me", "Please help me."),
        ("Come here", "Please come here."),
    ],
    "es": [
        ("Sí", "Sí."),
        ("No", "No."),
        ("Espera", "Espera un momento, por favor."),
        ("Gracias", "Gracias."),
        ("Luego", "Mejor luego."),
        ("No sé", "No estoy seguro."),
        ("Otra vez", "¿Puedes hacerlo otra vez?"),
        ("Para", "Para, por favor."),
        ("Ayúdame", "Ayúdame, por favor."),
        ("Ven", "Ven, por favor."),
    ],
}


def _part_of_day(hour: int) -> int:
    return 0 if 5 <= hour < 12 else 1 if 12 <= hour < 19 else 2


class FakeProvider:
    name = "fake"
    model = "fake"

    def __init__(self) -> None:
        self.calls: list[tuple[str, SuggestContext]] = []  # what was asked, for tests

    async def compose(self, ctx: SuggestContext) -> Sentences:
        self.calls.append(("compose", ctx))
        if ctx.path:
            topic = ctx.path[-1].rstrip(".?!…").lower()
            pool = [t.format(topic=topic, Topic=topic[:1].upper() + topic[1:]) for t in LEAF[ctx.lang]]
        else:
            part = PARTS[ctx.lang][_part_of_day(ctx.hour)]
            pool = [t.format(part=part) for t in NOW[ctx.lang]]
        known = ctx.shown + ((ctx.fixed_phrase,) if ctx.fixed_phrase else ())
        return Sentences.model_validate({"sentences": drop_known(pool, known)})

    async def more_options(self, ctx: SuggestContext) -> Options:
        self.calls.append(("more_options", ctx))
        shown = set(drop_known([label for label, _ in OPTIONS[ctx.lang]], ctx.shown))
        options = [{"label": label, "text": text} for label, text in OPTIONS[ctx.lang] if label in shown]
        return Options.model_validate({"options": options})

    async def aclose(self) -> None:
        pass
