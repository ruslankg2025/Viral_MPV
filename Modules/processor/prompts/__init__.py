from prompts.vision_default import VISION_DEFAULT
from prompts.vision_detailed import VISION_DETAILED
from prompts.vision_hooks import VISION_HOOKS_FOCUSED

VISION_PROMPTS = {
    "default": VISION_DEFAULT,
    "detailed": VISION_DETAILED,
    "hooks_focused": VISION_HOOKS_FOCUSED,
}


def get_prompt(template: str) -> str:
    return VISION_PROMPTS.get(template, VISION_DEFAULT)
