# Chroma集成指南

与LangChain、LlamaIndex和其他框架的集成。

## LangChain

```python
from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

vectorstore = Chroma.from_documents(
    documents=docs,
    embedding=OpenAIEmbeddings(),
    persist_directory="./chroma_db"
)

# 查询
results = vectorstore.similarity_search("query", k=3)

# 作为检索器
retriever = vectorstore.as_retriever()
```

## LlamaIndex

```python
from llama_index.vector_stores.chroma import ChromaVectorStore
import chromadb

db = chromadb.PersistentClient(path="./chroma_db")
collection = db.get_or_create_collection("docs")

vector_store = ChromaVectorStore(chroma_collection=collection)
```

## 资源

- **文档**：https://docs.trychroma.com
