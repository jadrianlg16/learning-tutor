"""
ai_providers.py

Reused from earlier work of mine on 2026-09-05 (715 lines
at copy time). No longer verbatim, in two places: the ten hardcoded 30-second
and 60-second httpx timeouts now read LT_LLM_TIMEOUT_S through the module-level
request_timeout() helper below (default 120 s), and ClaudeProvider follows the
current Messages API (no sampling parameters, room for adaptive thinking,
stop_reason checked, the first text block read). Everything else -- provider
set Google / OpenAI / Claude / Ollama / LM Studio / Custom, behind
AIProviderInterface + AIProviderFactory -- is unchanged.
See docs/modules/llm.md for how learning_tutor.llm wraps this module.
"""

import os
import json
import logging
import asyncio
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union
from enum import Enum
import httpx

# google-generativeai is an optional dependency: only required when the Google
# provider is actually used. Importing lazily keeps the backend installable for
# users who only run OpenAI / Ollama / LM Studio / custom providers.
try:
    import google.generativeai as genai
    from google.generativeai import GenerationConfig
except ImportError:  # pragma: no cover
    genai = None
    GenerationConfig = None

logger = logging.getLogger(__name__)

#: Fallback when this file is lifted out of learning_tutor and .config is not importable.
DEFAULT_TIMEOUT_S = 120.0


def request_timeout() -> float:
    """Seconds to wait on one provider HTTP call: LT_LLM_TIMEOUT_S, default 120.

    The original file hardcoded 30 s and 60 s. Read per
    call rather than at import, so a process can change it without a restart -- and so a
    test can monkeypatch the environment.
    """
    try:
        from .config import request_timeout as _configured
        return _configured()
    except Exception:  # pragma: no cover - defensive; keeps this file standalone
        try:
            return float(os.environ.get("LT_LLM_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        except ValueError:
            return DEFAULT_TIMEOUT_S


class AIProvider(str, Enum):
    GOOGLE = "google"
    OPENAI = "openai"
    CLAUDE = "claude"
    OLLAMA = "ollama"
    LM_STUDIO = "lm_studio"
    CUSTOM = "custom"

class AIProviderInterface(ABC):
    """Abstract interface for AI providers"""
    
    def __init__(self, api_key: str, model_name: str, base_url: Optional[str] = None):
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url
    
    @abstractmethod
    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None,
                           json_mode: bool = False, temperature: float = 0.1,
                           max_tokens: int = 4000) -> str:
        """Generate text response"""
        pass
    
    @abstractmethod
    async def generate_chat(self, messages: List[Dict[str, str]], 
                           temperature: float = 0.1) -> str:
        """Generate chat response"""
        pass
    
    @abstractmethod
    async def transcribe_audio(self, audio_data: bytes, mime_type: str, 
                              system_prompt: Optional[str] = None) -> str:
        """Transcribe audio (if supported)"""
        pass
    
    @abstractmethod
    def supports_audio(self) -> bool:
        """Check if provider supports audio transcription"""
        pass

class GoogleProvider(AIProviderInterface):
    """Google Gemini provider implementation"""
    
    def __init__(self, api_key: str, model_name: str, base_url: Optional[str] = None):
        super().__init__(api_key, model_name, base_url)
        if genai is None:
            raise ImportError(
                "The Google provider requires 'google-generativeai'. "
                "Install it with: pip install google-generativeai"
            )
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(model_name)
    
    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None,
                           json_mode: bool = False, temperature: float = 0.1,
                           max_tokens: int = 4000) -> str:
        try:
            config = GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
                response_mime_type="application/json" if json_mode else "text/plain"
            )
            
            if system_prompt:
                model = genai.GenerativeModel(
                    self.model_name,
                    system_instruction=system_prompt,
                    generation_config=config
                )
            else:
                model = genai.GenerativeModel(
                    self.model_name,
                    generation_config=config
                )
            
            response = model.generate_content(prompt)
            return response.text or ""
        
        except Exception as e:
            logger.error(f"Google provider text generation failed: {e}")
            raise
    
    async def generate_chat(self, messages: List[Dict[str, str]], 
                           temperature: float = 0.1) -> str:
        try:
            # Convert messages to Gemini format
            gemini_messages = []
            for msg in messages:
                role = "user" if msg["role"] == "user" else "model"
                gemini_messages.append({
                    "role": role,
                    "parts": [msg["content"]]
                })
            
            response = self.model.generate_content(gemini_messages)
            return response.text or ""
        
        except Exception as e:
            logger.error(f"Google provider chat generation failed: {e}")
            raise
    
    async def transcribe_audio(self, audio_data: bytes, mime_type: str, 
                              system_prompt: Optional[str] = None) -> str:
        try:
            config = GenerationConfig(
                response_mime_type="application/json"
            )
            
            if system_prompt:
                model = genai.GenerativeModel(
                    self.model_name,
                    system_instruction=system_prompt,
                    generation_config=config
                )
            else:
                model = self.model
            
            audio_blob = {"inline_data": {"mime_type": mime_type, "data": audio_data}}
            prompt = ["Transcribe this audio to JSON.", audio_blob]
            
            response = model.generate_content(prompt)
            return response.text or ""
        
        except Exception as e:
            logger.error(f"Google provider audio transcription failed: {e}")
            raise
    
    def supports_audio(self) -> bool:
        return True

class OpenAIProvider(AIProviderInterface):
    """OpenAI provider implementation with fixed transcription"""
    
    def __init__(self, api_key: str, model_name: str, base_url: Optional[str] = None):
        super().__init__(api_key, model_name, base_url)
        self.base_url = base_url or "https://api.openai.com/v1"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
    
    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None,
                           json_mode: bool = False, temperature: float = 0.1,
                           max_tokens: int = 4000) -> str:
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }

            if json_mode:
                payload["response_format"] = {"type": "json_object"}

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"] or ""

        except Exception as e:
            logger.error(f"OpenAI provider text generation failed: {e}")
            raise
    
    async def generate_chat(self, messages: List[Dict[str, str]], 
                           temperature: float = 0.1) -> str:
        try:
            # Convert to OpenAI format
            openai_messages = [
                {"role": msg["role"], "content": msg["content"]} 
                for msg in messages
            ]
            
            payload = {
                "model": self.model_name,
                "messages": openai_messages,
                "temperature": temperature,
                "max_tokens": 4000
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"] or ""
        
        except Exception as e:
            logger.error(f"OpenAI provider chat generation failed: {e}")
            raise
    
    async def transcribe_audio(self, audio_data: bytes, mime_type: str, 
                              system_prompt: Optional[str] = None) -> str:
        """Whisper transcription converted to ``{"segments": [...]}`` (one speaker per segment)."""
        try:
            # Step 1: Transcribe with Whisper API
            files = {
                "file": ("audio", audio_data, mime_type),
                "model": ("", "whisper-1"),
                "response_format": ("", "verbose_json"),  # Get timestamps
                "timestamp_granularities[]": ("", "segment")  # Get segment-level timestamps
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files=files,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                whisper_data = response.json()
            
            # Step 2: Convert Whisper output to our structured format
            structured_segments = []
            
            if "segments" in whisper_data:
                # Use Whisper's segment data
                for i, segment in enumerate(whisper_data["segments"]):
                    structured_segments.append({
                        "speaker": f"SPEAKER_{i:02d}",  # Simple speaker assignment
                        "text": segment.get("text", "").strip(),
                        "start": float(segment.get("start", 0)),
                        "end": float(segment.get("end", 0))
                    })
            else:
                # Fallback: create single segment from full text
                full_text = whisper_data.get("text", "")
                duration = whisper_data.get("duration", 0)
                structured_segments.append({
                    "speaker": "SPEAKER_00",
                    "text": full_text.strip(),
                    "start": 0.0,
                    "end": float(duration) if duration else 0.0
                })
            
            # Return as JSON string
            return json.dumps({"segments": structured_segments})
        
        except Exception as e:
            logger.error(f"OpenAI provider audio transcription failed: {e}")
            # Return fallback structure
            return json.dumps({
                "segments": [{
                    "speaker": "SPEAKER_00",
                    "text": f"[Transcription failed: {str(e)}]",
                    "start": 0.0,
                    "end": 0.0
                }]
            })
    
    def supports_audio(self) -> bool:
        return True

class ClaudeRefusalError(RuntimeError):
    """Claude declined the request (``stop_reason: "refusal"``)."""


class ClaudeTruncatedError(RuntimeError):
    """Claude stopped at ``max_tokens`` before finishing its answer."""


#: Floor for ``max_tokens`` on Claude calls. Current Claude models (e.g. claude-opus-5-5)
#: always run adaptive thinking, and thinking tokens count toward ``max_tokens``.
CLAUDE_MIN_MAX_TOKENS = 16000


def _claude_text(data: Dict[str, Any]) -> str:
    """The answer text of a Messages API response, after checking why it stopped.

    Thinking blocks can come before the text, so this reads the first block whose type
    is ``text`` rather than ``content[0]``.
    """
    stop_reason = data.get("stop_reason")
    if stop_reason == "refusal":
        details = data.get("stop_details") or {}
        category = details.get("category") if isinstance(details, dict) else None
        raise ClaudeRefusalError(
            f"Claude declined the request (stop_reason=refusal, category={category!r})"
        )
    if stop_reason == "max_tokens":
        raise ClaudeTruncatedError(
            "Claude stopped at max_tokens before finishing; the answer is truncated. "
            "Raise LT_LLM_MAX_TOKENS."
        )
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text") or ""
    return ""


class ClaudeProvider(AIProviderInterface):
    """Anthropic Claude provider implementation (raw HTTP to the Messages API).

    Current Claude models reject non-default sampling parameters with a 400, so no
    ``temperature`` is sent; the argument is accepted and ignored to keep the shared
    provider interface.
    """
    
    def __init__(self, api_key: str, model_name: str, base_url: Optional[str] = None):
        super().__init__(api_key, model_name, base_url)
        self.base_url = base_url or "https://api.anthropic.com/v1"
        self.headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json"
        }
    
    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None,
                           json_mode: bool = False, temperature: float = 0.1,
                           max_tokens: int = 4000) -> str:
        try:
            payload = {
                "model": self.model_name,
                "max_tokens": max(max_tokens, CLAUDE_MIN_MAX_TOKENS),
                "messages": [{"role": "user", "content": prompt}]
            }

            if system_prompt:
                payload["system"] = system_prompt
            
            if json_mode:
                # Claude doesn't have a direct JSON mode, but we can request JSON in the prompt
                json_instruction = "\n\nPlease respond with valid JSON only."
                payload["messages"][0]["content"] += json_instruction
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/messages",
                    headers=self.headers,
                    json=payload,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                return _claude_text(response.json())
        
        except Exception as e:
            logger.error(f"Claude provider text generation failed: {e}")
            raise
    
    async def generate_chat(self, messages: List[Dict[str, str]], 
                           temperature: float = 0.1) -> str:
        try:
            # Convert to Claude format
            claude_messages = []
            system_message = None
            
            for msg in messages:
                if msg["role"] == "system":
                    system_message = msg["content"]
                else:
                    claude_messages.append({
                        "role": "user" if msg["role"] == "user" else "assistant",
                        "content": msg["content"]
                    })
            
            payload = {
                "model": self.model_name,
                "max_tokens": CLAUDE_MIN_MAX_TOKENS,
                "messages": claude_messages
            }
            
            if system_message:
                payload["system"] = system_message
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/messages",
                    headers=self.headers,
                    json=payload,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                return _claude_text(response.json())
        
        except Exception as e:
            logger.error(f"Claude provider chat generation failed: {e}")
            raise
    
    async def transcribe_audio(self, audio_data: bytes, mime_type: str, 
                              system_prompt: Optional[str] = None) -> str:
        # Claude doesn't support direct audio transcription
        raise NotImplementedError("Claude doesn't support audio transcription")
    
    def supports_audio(self) -> bool:
        return False

class OllamaProvider(AIProviderInterface):
    """Ollama local provider implementation"""
    
    def __init__(self, api_key: str, model_name: str, base_url: Optional[str] = None):
        super().__init__(api_key, model_name, base_url)
        self.base_url = base_url or "http://localhost:11434"
    
    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None,
                           json_mode: bool = False, temperature: float = 0.1,
                           max_tokens: int = 4000) -> str:
        try:
            payload = {
                "model": self.model_name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens
                }
            }
            
            if system_prompt:
                payload["system"] = system_prompt
            
            if json_mode:
                payload["format"] = "json"
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                data = response.json()
                return data.get("response", "")
        
        except Exception as e:
            logger.error(f"Ollama provider text generation failed: {e}")
            raise
    
    async def generate_chat(self, messages: List[Dict[str, str]], 
                           temperature: float = 0.1) -> str:
        try:
            # Convert to Ollama format
            ollama_messages = [
                {"role": msg["role"], "content": msg["content"]} 
                for msg in messages
            ]
            
            payload = {
                "model": self.model_name,
                "messages": ollama_messages,
                "stream": False,
                "options": {
                    "temperature": temperature
                }
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                data = response.json()
                return data["message"]["content"] or ""
        
        except Exception as e:
            logger.error(f"Ollama provider chat generation failed: {e}")
            raise
    
    async def transcribe_audio(self, audio_data: bytes, mime_type: str, 
                              system_prompt: Optional[str] = None) -> str:
        # Ollama doesn't support direct audio transcription
        raise NotImplementedError("Ollama doesn't support audio transcription")
    
    def supports_audio(self) -> bool:
        return False

class LMStudioProvider(AIProviderInterface):
    """LM Studio local provider implementation"""
    
    def __init__(self, api_key: str, model_name: str, base_url: Optional[str] = None):
        super().__init__(api_key, model_name, base_url)
        self.base_url = base_url or "http://localhost:1234/v1"
        self.headers = {
            "Content-Type": "application/json"
        }
        if api_key and api_key != "not-needed":
            self.headers["Authorization"] = f"Bearer {api_key}"
    
    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None,
                           json_mode: bool = False, temperature: float = 0.1,
                           max_tokens: int = 4000) -> str:
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"] or ""
        
        except Exception as e:
            logger.error(f"LM Studio provider text generation failed: {e}")
            raise
    
    async def generate_chat(self, messages: List[Dict[str, str]], 
                           temperature: float = 0.1) -> str:
        try:
            payload = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 4000
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"] or ""
        
        except Exception as e:
            logger.error(f"LM Studio provider chat generation failed: {e}")
            raise
    
    async def transcribe_audio(self, audio_data: bytes, mime_type: str, 
                              system_prompt: Optional[str] = None) -> str:
        # LM Studio doesn't support direct audio transcription
        raise NotImplementedError("LM Studio doesn't support audio transcription")
    
    def supports_audio(self) -> bool:
        return False

class CustomProvider(AIProviderInterface):
    """Custom API provider implementation (OpenAI-compatible)"""
    
    def __init__(self, api_key: str, model_name: str, base_url: Optional[str] = None):
        super().__init__(api_key, model_name, base_url)
        if not base_url:
            raise ValueError("Custom provider requires base_url")
        self.headers = {
            "Content-Type": "application/json"
        }
        if api_key:
            self.headers["Authorization"] = f"Bearer {api_key}"
    
    async def generate_text(self, prompt: str, system_prompt: Optional[str] = None,
                           json_mode: bool = False, temperature: float = 0.1,
                           max_tokens: int = 4000) -> str:
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            payload = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
            
            if json_mode:
                payload["response_format"] = {"type": "json_object"}
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json=payload,
                    timeout=request_timeout()
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"] or ""
        
        except Exception as e:
            logger.error(f"Custom provider text generation failed: {e}")
            raise
    
    async def generate_chat(self, messages: List[Dict[str, str]], 
                           temperature: float = 0.1) -> str:
        return await self.generate_text(
            messages[-1]["content"], 
            system_prompt=messages[0]["content"] if messages[0]["role"] == "system" else None,
            temperature=temperature
        )
    
    async def transcribe_audio(self, audio_data: bytes, mime_type: str, 
                              system_prompt: Optional[str] = None) -> str:
        raise NotImplementedError("Custom provider audio support depends on implementation")
    
    def supports_audio(self) -> bool:
        return False

class AIProviderFactory:
    """Factory for creating AI provider instances"""
    
    @staticmethod
    def create_provider(provider: AIProvider, api_key: str, model_name: str, 
                       base_url: Optional[str] = None) -> AIProviderInterface:
        """Create an AI provider instance"""
        
        if provider == AIProvider.GOOGLE:
            return GoogleProvider(api_key, model_name, base_url)
        elif provider == AIProvider.OPENAI:
            return OpenAIProvider(api_key, model_name, base_url)
        elif provider == AIProvider.CLAUDE:
            return ClaudeProvider(api_key, model_name, base_url)
        elif provider == AIProvider.OLLAMA:
            return OllamaProvider(api_key, model_name, base_url)
        elif provider == AIProvider.LM_STUDIO:
            return LMStudioProvider(api_key, model_name, base_url)
        elif provider == AIProvider.CUSTOM:
            return CustomProvider(api_key, model_name, base_url)
        else:
            raise ValueError(f"Unsupported AI provider: {provider}")
    
    @staticmethod
    def get_available_providers() -> List[str]:
        """Get list of available AI providers"""
        return [provider.value for provider in AIProvider]
