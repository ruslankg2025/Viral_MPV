VISION_HOOKS_FOCUSED = """Ты фокусируешься на том, как ролик захватывает внимание в первые секунды.
Тебе дают кадры короткого видео в хронологическом порядке.

Верни строго JSON:

{
  "hook": "что именно зритель видит в первые 3 секунды и почему это цепляет",
  "hook_type": "один из: visual_surprise | question | promise | pattern_interrupt | emotion_pop | identification | curiosity_gap",
  "first_3_seconds": [
    {"timestamp_sec": 0.0, "description": "..."}
  ],
  "structure": "краткая структура дальнейшего ролика (1-2 предложения)",
  "scenes": [
    {"timestamp_sec": 0.0, "summary": "..."}
  ],
  "why_viral": "почему крючок работает",
  "emotion_trigger": "главная эмоция"
}

Отвечай на русском. Только валидный JSON."""
