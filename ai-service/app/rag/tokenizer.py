"""中英混合分词器：不依赖任何第三方分词库（W3-3 关键词检索基线档）。

设计取舍（为什么不用 jieba）：

- **零依赖、可离线**：W3 的验收要求「不依赖 API Key、不依赖 PostgreSQL、不依赖大模型」。
  jieba 虽然轻量，但会把「可离线跑」的 RAG 基线又绑到一个第三方包上；而本项目的
  语料只有 ~500 本书 + 规则 + FAQ，关键词检索用「中文 unigram + bigram」已足够。
- **可解释**：unigram 保证「三体」「清朝」这类精确词不会漏召回；bigram 提供短语级
  区分度（如「逾期」vs「期罚」），对 BM25 的 IDF 统计非常友好。
- **确定性强**：纯规则切分没有词典 OOV 问题，同一文本任何时候切分结果一致，便于测试。

切分规则：

1. 先做 NFKC 归一化（全角转半角）+ 小写（对英文/数字）；
2. 连续 CJK 字符段 → 逐字 unigram + 相邻 bigram；
3. 英文单词 → 整词（保留内部数字，如 ``java21``）；数字（含小数）→ 整词；
4. 最后精确匹配停用词表过滤（只丢整 token，不影响 ``借书`` 这类 bigram）。
"""

from __future__ import annotations

import re
import unicodedata

# CJK 统一表意文字（含扩展 A）与常见中文标点之外的部分
_CJK_RUN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_TOKEN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+|[A-Za-z][A-Za-z0-9_]*|\d+(?:\.\d+)?")

# 中文停用词：单字虚词 / 代词 / 疑问词 / 无检索价值的泛化词。
# 只影响单字 token，像「借书」「历史」这种 bigram 仍会保留，避免把语义切碎。
CJK_STOPWORDS = frozenset(
    {
        # 虚词 / 助词
        "的", "了", "是", "在", "有", "和", "就", "不", "人", "都", "一", "上", "也",
        "很", "到", "说", "要", "去", "你", "会", "着", "没", "看", "好", "这", "那",
        "我", "他", "她", "它", "们", "个", "与", "及", "或", "但", "而", "被", "把",
        "从", "对", "为", "以", "于", "之", "其", "吗", "呢", "吧", "呀", "啊", "哦",
        "嗯", "请", "帮", "想", "能", "可", "该", "更", "最", "再", "又", "还", "让",
        "得", "地", "过", "开", "下", "中", "内", "外", "等", "每", "各", "些", "哪",
        "怎", "么", "什", "啥", "嘛", "呗", "哈", "哎",
        # 泛化名词（单字时几乎没有区分度）
        "书", "本",
        # 多字虚词 / 套话
        "我们", "你们", "他们", "她们", "它们", "自己", "这个", "那个", "这些", "那些",
        "什么", "怎么", "怎样", "如何", "为什么", "哪些", "哪个", "多少", "是否",
        "是不是", "有没有", "没有", "可以", "能否", "需要", "应该", "可能", "已经", "现在",
        "然后", "但是", "而且", "因为", "所以", "如果", "虽然", "只是", "就是", "还是",
        "一些", "一下", "请问", "帮忙", "帮我", "告诉", "我想", "我要", "你好", "您好",
        "谢谢", "一般", "通常",
    }
)

# 英文停用词（小写形式）
EN_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "both", "but", "by",
        "can", "could", "did", "do", "does", "each", "few", "for", "from", "had", "has",
        "have", "he", "her", "here", "him", "his", "how", "i", "if", "in", "into", "is",
        "it", "its", "just", "may", "me", "might", "more", "most", "my", "no", "not",
        "of", "off", "on", "only", "or", "other", "our", "out", "over", "own", "same",
        "she", "should", "so", "some", "such", "than", "that", "the", "their", "them",
        "then", "there", "these", "they", "this", "those", "through", "to", "too", "under",
        "until", "up", "us", "very", "was", "we", "were", "what", "when", "where", "which",
        "while", "who", "why", "will", "with", "would", "you", "your",
    }
)

STOPWORDS = CJK_STOPWORDS | EN_STOPWORDS


def is_cjk(char: str) -> bool:
    """单个字符是否为 CJK 表意文字。"""
    return bool(char) and bool(_CJK_RUN_RE.fullmatch(char))


def normalize(text: str) -> str:
    """NFKC 归一化（全角→半角）+ 小写，统一英文/数字的检索形态。"""
    return unicodedata.normalize("NFKC", text or "").lower()


def tokenize(text: str, *, drop_stopwords: bool = True) -> list[str]:
    """把一段中英混合文本切成检索 token 列表。

    - 中文字符段：逐字 unigram + 相邻 bigram（如「清朝历史」→ 清/朝/历/史/清朝/朝历/历史）
    - 英文/数字：按整词切分并小写（如「Java 21」→ java/21）
    - ``drop_stopwords=True`` 时只过滤与停用词完全相等的 token，不会拆散 bigram
    """
    if not text:
        return []
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(normalize(str(text))):
        piece = match.group(0)
        if _CJK_RUN_RE.fullmatch(piece):
            chars = list(piece)
            tokens.extend(chars)  # unigram：保证精确书名/分类词不漏召回
            tokens.extend(chars[i] + chars[i + 1] for i in range(len(chars) - 1))  # bigram
        else:
            tokens.append(piece)
    if drop_stopwords:
        tokens = [token for token in tokens if token not in STOPWORDS]
    return tokens
