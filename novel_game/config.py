"""配置中心：DeepSeek LLM + ChromaDB + Embedding"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# HuggingFace 国内镜像（必须在 import transformers/huggingface_hub 之前设置，
# 供中文 Embedding 模型首次下载使用）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# 项目根目录
BASE_DIR = Path(__file__).parent

# 数据目录
DATA_DIR = BASE_DIR / "data"
NOVELS_DIR = DATA_DIR / "novels"
CHROMA_DIR = BASE_DIR / "chroma_db"
SESSIONS_DIR = DATA_DIR / "sessions"

# 确保目录存在
NOVELS_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

# DeepSeek LLM 配置
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "deepseek-chat")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "16384"))

# Embedding 配置
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5")

# Embedding 函数单例（懒加载，避免重复加载模型）
_embedding_function = None


def get_embedding_function():
    """
    构建中文 Embedding 函数，供 ChromaDB 入库/检索使用。

    使用 sentence-transformers 加载 bge-base-zh-v1.5（中文向量模型，纯CPU可跑）。
    首次调用会从 HF_ENDPOINT 镜像下载模型（约400MB），之后走本地缓存。
    依赖缺失时明确报错，不静默回退到英文默认模型。
    """
    global _embedding_function
    if _embedding_function is not None:
        return _embedding_function

    try:
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError as e:
        raise ImportError(
            "未安装 sentence-transformers，无法使用中文 Embedding 模型。\n"
            "请运行: pip install sentence-transformers"
        ) from e

    _embedding_function = SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL,
        device="cpu",
    )
    return _embedding_function

# RAG 检索配置
RETRIEVAL_TOP_K = 5          # 长期记忆检索返回段落数
SHORT_TERM_LIMIT = 5         # 短期记忆保留轮数
CHUNK_SIZE = 800             # 小说切片字数
CHUNK_OVERLAP = 100         # 切片重叠字数

# ===== 共享单例 =====

_openai_client = None


def get_llm_client():
    """共享的 DeepSeek OpenAI Client 单例（4 处 agents + character_extractor 共用）"""
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        # timeout：单次请求 60s 上限，防止 API 挂起导致请求永久阻塞
        # max_retries：SDK 内置对 429/5xx 的指数退避重试
        _openai_client = OpenAI(
            api_key=LLM_API_KEY, base_url=LLM_BASE_URL,
            timeout=60.0, max_retries=2,
        )
    return _openai_client
