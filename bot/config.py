import html
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml
import dotenv
from pathlib import Path

logger = logging.getLogger(__name__)

config_dir = Path(__file__).parent.parent.resolve() / "config"

# load yaml config
with open(config_dir / "config.yml", 'r') as f:
    config_yaml = yaml.safe_load(f)

# load .env config
config_env = dotenv.dotenv_values(config_dir / "config.env")

# config parameters
telegram_token = config_yaml["telegram_token"]
openai_api_key = config_yaml["openai_api_key"]
openai_api_base = config_yaml.get("openai_api_base", None)
openrouter_api_key = config_yaml.get("openrouter_api_key", None)
openrouter_api_base = config_yaml.get(
    "openrouter_api_base", "https://openrouter.ai/api/v1")
allowed_telegram_usernames = config_yaml["allowed_telegram_usernames"]
enable_message_streaming = config_yaml.get("enable_message_streaming", True)
return_n_generated_images = config_yaml.get("return_n_generated_images", 1)
image_size = config_yaml.get("image_size", "1024x1024")
image_model = str(config_yaml.get("image_model") or "gpt-image-1").strip()
audio_model = str(config_yaml.get("audio_model") or "whisper-1").strip()
n_chat_modes_per_page = config_yaml.get("n_chat_modes_per_page", 5)
mongodb_uri = f"mongodb://mongo:{config_env['MONGODB_PORT']}"

# chat_modes
chat_modes_path = config_dir / "chat_modes.yml"
if not chat_modes_path.exists():
    chat_modes_path = config_dir / "chat_modes.example.yml"
with open(chat_modes_path, 'r') as f:
    chat_modes = yaml.safe_load(f)

# models
models_path = config_dir / "models.yml"
if not models_path.exists():
    models_path = config_dir / "models.example.yml"
with open(models_path, 'r') as f:
    models = yaml.safe_load(f)


def _fetch_provider_model_ids() -> list[str]:
    """Read the model catalog from the configured OpenAI-compatible provider."""
    if not openai_api_base or not openai_api_key:
        return []

    request = Request(
        f"{openai_api_base.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {openai_api_key}"},
    )
    try:
        with urlopen(request, timeout=3) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        # Keep the static YAML as a fallback when the provider is unavailable
        # during startup. Never include the key or response body in logs.
        logger.warning("Could not load provider model catalog: %s", type(exc).__name__)
        return []

    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        logger.warning("Provider model catalog has an unexpected format")
        return []

    model_ids = []
    for item in payload["data"]:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        # ChatGPT2API exposes image models through the same catalog. They are
        # handled by the bot's artist flow, not by ordinary text chat.
        if (
            model_id
            and not model_id.startswith(("gpt-image-", "codex-gpt-image-"))
            and model_id not in model_ids
        ):
            model_ids.append(model_id)
    return model_ids


def _provider_model_info(model_id: str, static_info: dict) -> dict:
    """Build metadata required by the settings and usage screens."""
    info = dict(static_info)
    info.update(
        {
            "type": "chat_completion",
            "provider": "openai",
            "vision": info.get("vision", True),
            "name": info.get("name") or model_id,
            "description": info.get(
                "description",
                f"Model <b>{html.escape(model_id)}</b> from the configured ChatGPT2API provider.",
            ),
            "price_per_1000_input_tokens": info.get("price_per_1000_input_tokens", 0.0),
            "price_per_1000_output_tokens": info.get("price_per_1000_output_tokens", 0.0),
            "scores": info.get("scores", {"Smart": 3, "Fast": 3, "Cheap": 3}),
        }
    )
    return info


def _merge_provider_models(static_models: dict) -> dict:
    provider_model_ids = _fetch_provider_model_ids()
    if not provider_model_ids:
        return static_models

    info = dict(static_models.get("info") or {})
    for model_id in provider_model_ids:
        info[model_id] = _provider_model_info(model_id, info.get(model_id, {}))

    logger.info(
        "Loaded %d models from the configured provider: %s",
        len(provider_model_ids),
        ", ".join(provider_model_ids),
    )
    return {
        **static_models,
        "available_text_models": provider_model_ids,
        "info": info,
    }


models = _merge_provider_models(models)

configured_default_text_model = str(config_yaml.get("default_text_model") or "").strip()
default_text_model = (
    configured_default_text_model
    if configured_default_text_model in models["available_text_models"]
    else models["available_text_models"][0]
)

# files
help_group_chat_video_path = Path(
    __file__).parent.parent.resolve() / "static" / "help_group_chat.mp4"
