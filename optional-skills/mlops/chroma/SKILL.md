---
name: chroma
description: 开源嵌入数据库，用于AI应用。存储嵌入和元数据，执行向量和全文搜索，按元数据过滤。简单4函数API。从笔记本扩展到生产集群。用于语义搜索、RAG应用或文档检索。最适合本地开发和开源项目。
version: 1.0.0
author: Orchestra Research
license: MIT
dependencies: [chromadb, sentence-transformers]
metadata:
  kclaw:
    tags: [RAG, Chroma, 向量数据库, 嵌入, 语义搜索, 开源, 自托管, 文档检索, 元数据过滤]

---

# Chroma - 开源嵌入数据库

用于构建带记忆的LLM应用的AI原生数据库。

## 何时使用Chroma

**在以下情况下使用Chroma：**
- 构建RAG（检索增强生成）应用
- 需要本地/自托管向量数据库
- 想要开源解决方案（Apache 2.0）
- 在笔记本中原型开发
- 对文档进行语义搜索
- 存储带元数据的嵌入

**指标**：
- **24,300+ GitHub stars**
- **1,900+ forks**
- **v1.3.3**（稳定，每周发布）
- **Apache 2.0许可证**

**使用替代方案**：
- **Pinecone**：托管云，自动扩展
- **FAISS**：纯相似性搜索，无元数据
- **Weaviate**：生产ML原生数据库
- **Qdrant**：高性能，Rust驱动

## 快速开始

### 安装

```bash
# Python
pip install chromadb

# JavaScript/TypeScript
npm install chromadb @chroma-core/default-embed
```

### 基础用法（Python）

```python
import chromadb

# 创建客户端
client = chromadb.Client()

# 创建集合
collection = client.create_collection(name="my_collection")

# 添加文档
collection.add(
    documents=["This is document 1", "This is document 2"],
    metadatas=[{"source": "doc1"}, {"source": "doc2"}],
    ids=["id1", "id2"]
)

# 查询
results = collection.query(
    query_texts=["document about topic"],
    n_results=2
)

print(results)
```

## 核心操作

### 1. 创建集合

```python
# 简单集合
collection = client.create_collection("my_docs")

# 使用自定义嵌入函数
from chromadb.utils import embedding_functions

openai_ef = embedding_functions.OpenAIEmbeddingFunction(
    api_key="your-key",
    model_name="text-embedding-3-small"
)

collection = client.create_collection(
    name="my_docs",
    embedding_function=openai_ef
)

# 获取现有集合
collection = client.get_collection("my_docs")

# 删除集合
client.delete_collection("my_docs")
```

### 2. 添加文档

```python
# 添加自动生成的ID
collection.add(
    documents=["Doc 1", "Doc 2", "Doc 3"],
    metadatas=[
        {"source": "web", "category": "tutorial"},
        {"source": "pdf", "page": 5},
        {"source": "api", "timestamp": "2025-01-01"}
    ],
    ids=["id1", "id2", "id3"]
)

# 添加自定义嵌入
collection.add(
    embeddings=[[0.1, 0.2, ...], [0.3, 0.4, ...]],
    documents=["Doc 1", "Doc 2"],
    ids=["id1", "id2"]
)
```

### 3. 查询（相似性搜索）

```python
# 基本查询
results = collection.query(
    query_texts=["machine learning tutorial"],
    n_results=5
)

# 带过滤器的查询
results = collection.query(
    query_texts=["Python programming"],
    n_results=3,
    where={"source": "web"}
)

# 带元数据过滤器的查询
results = collection.query(
    query_texts=["advanced topics"],
    where={
        "$and": [
            {"category": "tutorial"},
            {"difficulty": {"$gte": 3}}
        ]
    }
)

# 访问结果
print(results["documents"])      # 匹配文档列表
print(results["metadatas"])      # 每个文档的元数据
print(results["distances"])      # 相似度分数
print(results["ids"])            # 文档ID
```

### 4. 获取文档

```python
# 按ID获取
docs = collection.get(
    ids=["id1", "id2"]
)

# 带过滤器获取
docs = collection.get(
    where={"category": "tutorial"},
    limit=10
)

# 获取所有文档
docs = collection.get()
```

### 5. 更新文档

```python
# 更新文档内容
collection.update(
    ids=["id1"],
    documents=["Updated content"],
    metadatas=[{"source": "updated"}]
)
```

### 6. 删除文档

```python
# 按ID删除
collection.delete(ids=["id1", "id2"])

# 带过滤器删除
collection.delete(
    where={"source": "outdated"}
)
```

## 持久化存储

```python
# 持久化到磁盘
client = chromadb.PersistentClient(path="./chroma_db")

collection = client.create_collection("my_docs")
collection.add(documents=["Doc 1"], ids=["id1"])

# 数据自动持久化
# 之后用相同路径重新加载
client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_collection("my_docs")
```

## 嵌入函数

### 默认（Sentence Transformers）

```python
# 默认使用sentence-transformers
collection = client.create_collection("my_docs")
# 默认模型：all-MiniLM-L6-v2
```

### OpenAI

```python
from chromadb.utils import embedding_functions

openai_ef = embedding_functions.OpenAIEmbeddingFunction(
    api_key="your-key",
    model_name="text-embedding-3-small"
)

collection = client.create_collection(
    name="openai_docs",
    embedding_function=openai_ef
)
```

### HuggingFace

```python
huggingface_ef = embedding_functions.HuggingFaceEmbeddingFunction(
    api_key="your-key",
    model_name="sentence-transformers/all-mpnet-base-v2"
)

collection = client.create_collection(
    name="hf_docs",
    embedding_function=huggingface_ef
)
```

### 自定义嵌入函数

```python
from chromadb import Documents, EmbeddingFunction, Embeddings

class MyEmbeddingFunction(EmbeddingFunction):
    def __call__(self, input: Documents) -> Embeddings:
        # 你的嵌入逻辑
        return embeddings

my_ef = MyEmbeddingFunction()
collection = client.create_collection(
    name="custom_docs",
    embedding_function=my_ef
)
```

## 元数据过滤

```python
# 精确匹配
results = collection.query(
    query_texts=["query"],
    where={"category": "tutorial"}
)

# 比较运算符
results = collection.query(
    query_texts=["query"],
    where={"page": {"$gt": 10}}  # $gt, $gte, $lt, $lte, $ne
)

# 逻辑运算符
results = collection.query(
    query_texts=["query"],
    where={
        "$and": [
            {"category": "tutorial"},
            {"difficulty": {"$lte": 3}}
        ]
    }  # 也支持: $or
)

# 包含
results = collection.query(
    query_texts=["query"],
    where={"tags": {"$in": ["python", "ml"]}}
)
```

## LangChain集成

```python
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter

# 分割文档
text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000)
docs = text_splitter.split_documents(documents)

# 创建Chroma向量存储
vectorstore = Chroma.from_documents(
    documents=docs,
    embedding=OpenAIEmbeddings(),
    persist_directory="./chroma_db"
)

# 查询
results = vectorstore.similarity_search("machine learning", k=3)

# 作为检索器
retriever = vectorstore.as_retriever(search_kwargs={"k": 5})
```

## LlamaIndex集成

```python
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core import VectorStoreIndex, StorageContext
import chromadb

# 初始化Chroma
db = chromadb.PersistentClient(path="./chroma_db")
collection = db.get_or_create_collection("my_collection")

# 创建向量存储
vector_store = ChromaVectorStore(chroma_collection=collection)
storage_context = StorageContext.from_defaults(vector_store=vector_store)

# 创建索引
index = VectorStoreIndex.from_documents(
    documents,
    storage_context=storage_context
)

# 查询
query_engine = index.as_query_engine()
response = query_engine.query("What is machine learning?")
```

## 服务器模式

```python
# 运行Chroma服务器
# 终端: chroma run --path ./chroma_db --port 8000

# 连接到服务器
import chromadb
from chromadb.config import Settings

client = chromadb.HttpClient(
    host="localhost",
    port=8000,
    settings=Settings(anonymized_telemetry=False)
)

# 像平常一样使用
collection = client.get_or_create_collection("my_docs")
```

## 最佳实践

1. **使用持久化客户端** - 不会因重启丢失数据
2. **添加元数据** - 支持过滤和跟踪
3. **批量操作** - 一次添加多个文档
4. **选择正确的嵌入模型** - 平衡速度/质量
5. **使用过滤器** - 缩小搜索空间
6. **唯一ID** - 避免冲突
7. **定期备份** - 复制chroma_db目录
8. **监控集合大小** - 需要时扩展
9. **测试嵌入函数** - 确保质量
10. **生产环境使用服务器模式** - 多用户更好

## 性能

| 操作 | 延迟 | 说明 |
|-----------|---------|-------|
| 添加100个文档 | ~1-3秒 | 含嵌入 |
| 查询（top 10） | ~50-200毫秒 | 取决于集合大小 |
| 元数据过滤 | ~10-50毫秒 | 适当索引下很快 |

## 资源

- **GitHub**：https://github.com/chroma-core/chroma
- **文档**：https://docs.trychroma.com
- **Discord**：https://discord.gg/MMeYNTmh3x
- **版本**：1.3.3+
- **许可证**：Apache 2.0
