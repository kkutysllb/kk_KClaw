#!/usr/bin/env python3
"""
HuggingFace 数据集采样与压缩

从多个 HuggingFace 数据集下载轨迹数据，进行随机采样，
并运行轨迹压缩以符合目标 token 预算。

使用方法:
    python scripts/sample_and_compress.py
    
    # 自定义样本数量
    python scripts/sample_and_compress.py --total_samples=5000
    
    # 自定义输出名称
    python scripts/sample_and_compress.py --output_name=compressed_16k
"""

import json
import random
from pathlib import Path
from typing import List, Dict, Any, Tuple
import fire

# Load environment variables
from dotenv import load_dotenv
load_dotenv()


# Default datasets to sample from
DEFAULT_DATASETS = [
    "NousResearch/swe-terminus-agent-glm-kimi-minimax",
    "NousResearch/kclaw-megascience-sft1",
    "NousResearch/KClaw-Agent-Thinking-GLM-4.7-SFT2",
    "NousResearch/KClaw-Agent-Thinking-GLM-4.7-SFT1",
    "NousResearch/terminal-tasks-glm-kclaw"
]


def load_dataset_from_hf(dataset_name: str) -> List[Dict[str, Any]]:
    """
    Load a dataset from HuggingFace.
    
    Args:
        dataset_name: HuggingFace dataset name (e.g., "NousResearch/dataset-name")
        
    Returns:
        List of trajectory entries
    """
    from datasets import load_dataset
    
    print(f"   正在加载 {dataset_name}...")
    
    try:
        # Try loading with default config
        ds = load_dataset(dataset_name, split="train")
    except Exception as e:
        print(f"   ⚠️  加载 {dataset_name} 时出错: {e}")
        return []
    
    # Convert to list of dicts
    entries = []
    for item in ds:
        # Handle different possible formats
        if "conversations" in item:
            entries.append({"conversations": item["conversations"]})
        elif "messages" in item:
            # Convert messages format to conversations format if needed
            entries.append({"conversations": item["messages"]})
        else:
            # Assume the whole item is the entry
            entries.append(dict(item))
    
    print(f"   ✅ 已从 {dataset_name} 加载 {len(entries):,} 条记录")
    return entries


# Global tokenizer for multiprocessing (set in worker init)
_TOKENIZER = None


def _init_tokenizer_worker(tokenizer_name: str):
    """Initialize tokenizer in worker process."""
    global _TOKENIZER
    from transformers import AutoTokenizer
    _TOKENIZER = AutoTokenizer.from_pretrained(tokenizer_name, trust_remote_code=True)


def _count_tokens_for_entry(entry: Dict) -> Tuple[Dict, int]:
    """
    Count tokens for a single entry (used in parallel processing).
    
    Args:
        entry: Trajectory entry with 'conversations' field
        
    Returns:
        Tuple of (entry, token_count)
    """
    global _TOKENIZER
    
    conversations = entry.get("conversations", [])
    if not conversations:
        return entry, 0
    
    total = 0
    for turn in conversations:
        value = turn.get("value", "")
        if value:
            try:
                total += len(_TOKENIZER.encode(value))
            except Exception:
                # Fallback to character estimate
                total += len(value) // 4
    
    return entry, total


def sample_from_datasets(
    datasets: List[str],
    total_samples: int,
    min_tokens: int = 16000,
    tokenizer_name: str = "moonshotai/Kimi-K2-Thinking",
    seed: int = 42,
    num_proc: int = 8
) -> List[Dict[str, Any]]:
    """
    Load all datasets, filter by token count, then randomly sample from combined pool.
    
    Args:
        datasets: List of HuggingFace dataset names
        total_samples: Total number of samples to collect
        min_tokens: Minimum token count to include (only sample trajectories >= this)
        tokenizer_name: HuggingFace tokenizer for counting tokens
        seed: Random seed for reproducibility
        num_proc: Number of parallel processes for tokenization
        
    Returns:
        List of sampled trajectory entries
    """
    from multiprocessing import Pool
    
    random.seed(seed)
    
    print(f"\n📥 正在加载 {len(datasets)} 个数据集...")
    print(f"   最低 token 数: {min_tokens:,} (过滤较小的轨迹)")
    print(f"   并行工作线程: {num_proc}")
    print()
    
    # Load ALL entries from all datasets into one pool
    all_entries = []
    
    for dataset_name in datasets:
        entries = load_dataset_from_hf(dataset_name)
        
        if not entries:
            print(f"   ⚠️  跳过 {dataset_name} (未加载到记录)")
            continue
        
        # Add source metadata to each entry
        for entry in entries:
            entry["_source_dataset"] = dataset_name
        
        all_entries.extend(entries)
    
    print(f"\n📊 共加载 {len(all_entries):,} 条记录")
    
    # Filter by token count using parallel processing
    print(f"\n🔍 正在筛选 token 数 >= {min_tokens:,} 的轨迹 (使用 {num_proc} 个工作线程)...")
    
    filtered_entries = []
    token_counts = []
    
    # Use multiprocessing for token counting
    with Pool(
        processes=num_proc,
        initializer=_init_tokenizer_worker,
        initargs=(tokenizer_name,)
    ) as pool:
        # Process in chunks and show progress
        chunk_size = 1000
        processed = 0
        
        for result in pool.imap_unordered(_count_tokens_for_entry, all_entries, chunksize=100):
            entry, token_count = result
            processed += 1
            
            if processed % chunk_size == 0:
                print(f"   已处理 {processed:,}/{len(all_entries):,}...", end="\r")
            
            if token_count >= min_tokens:
                entry["_original_tokens"] = token_count
                filtered_entries.append(entry)
                token_counts.append(token_count)
    
    print(f"\n   ✅ 找到 {len(filtered_entries):,} 条轨迹，token 数 >= {min_tokens:,}")
    
    if token_counts:
        avg_tokens = sum(token_counts) / len(token_counts)
        print(f"   📈 Token 统计: 最小={min(token_counts):,}, 最大={max(token_counts):,}, 平均={avg_tokens:,.0f}")
    
    # Random sample from the filtered pool
    if len(filtered_entries) <= total_samples:
        print(f"\n⚠️  仅找到 {len(filtered_entries):,} 条轨迹，将全部使用")
        sampled = filtered_entries
    else:
        sampled = random.sample(filtered_entries, total_samples)
        print(f"\n✅ 已从 {len(filtered_entries):,} 条轨迹池中随机抽样 {len(sampled):,} 条")
    
    # Show source distribution
    source_counts = {}
    for entry in sampled:
        source = entry.get("_source_dataset", "unknown").split("/")[-1]
        source_counts[source] = source_counts.get(source, 0) + 1
    
    print(f"\n📌 按来源的抽样分布:")
    for source, count in sorted(source_counts.items()):
        print(f"      {source}: {count:,}")
    
    # Shuffle
    random.shuffle(sampled)
    
    return sampled


def save_samples_for_compression(
    samples: List[Dict[str, Any]],
    output_dir: Path,
    batch_size: int = 100
):
    """
    Save samples to JSONL files for trajectory compression.
    
    Args:
        samples: List of trajectory entries
        output_dir: Directory to save JSONL files
        batch_size: Number of entries per file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Split into batches
    num_batches = (len(samples) + batch_size - 1) // batch_size
    
    print(f"\n💾 正在保存 {len(samples)} 个样本到 {output_dir}")
    print(f"   批次大小: {batch_size}, 总批次数: {num_batches}")
    
    for i in range(num_batches):
        start_idx = i * batch_size
        end_idx = min((i + 1) * batch_size, len(samples))
        batch = samples[start_idx:end_idx]
        
        output_file = output_dir / f"batch_{i}.jsonl"
        with open(output_file, 'w', encoding='utf-8') as f:
            for entry in batch:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    
    print(f"   ✅ 已保存 {num_batches} 个批次文件")


def run_compression(input_dir: Path, output_dir: Path, config_path: str):
    """
    Run trajectory compression on the sampled data.
    
    Args:
        input_dir: Directory containing JSONL files to compress
        output_dir: Directory for compressed output
        config_path: Path to compression config YAML
    """
    # Import the compressor
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from trajectory_compressor import TrajectoryCompressor, CompressionConfig
    
    print(f"\n🗜️  正在运行轨迹压缩...")
    print(f"   输入: {input_dir}")
    print(f"   输出: {output_dir}")
    print(f"   配置: {config_path}")
    
    # Load config
    config = CompressionConfig.from_yaml(config_path)
    
    # Initialize compressor
    compressor = TrajectoryCompressor(config)
    
    # Run compression
    compressor.process_directory(input_dir, output_dir)


def merge_output_to_single_jsonl(input_dir: Path, output_file: Path):
    """
    Merge all JSONL files in a directory into a single JSONL file.
    
    Args:
        input_dir: Directory containing JSONL files
        output_file: Output JSONL file path
    """
    print(f"\n📦 正在合并输出文件到 {output_file.name}...")
    
    all_entries = []
    for jsonl_file in sorted(input_dir.glob("*.jsonl")):
        if jsonl_file.name == output_file.name:
            continue
        with open(jsonl_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    all_entries.append(json.loads(line))
    
    # Write merged file
    with open(output_file, 'w', encoding='utf-8') as f:
        for entry in all_entries:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    
    print(f"   ✅ 已合并 {len(all_entries):,} 条记录到 {output_file.name}")
    return output_file


def main(
    total_samples: int = 2500,
    output_name: str = "compressed_agentic",
    datasets: str = None,
    config: str = "configs/trajectory_compression.yaml",
    seed: int = 42,
    batch_size: int = 100,
    min_tokens: int = 16000,
    num_proc: int = 8,
    skip_download: bool = False,
):
    """
    Sample trajectories from HuggingFace datasets and run compression.
    
    Args:
        total_samples: Total number of samples to collect (default: 2500)
        output_name: Name for output directory/file (default: "compressed_agentic")
        datasets: Comma-separated list of dataset names (uses defaults if not provided)
        config: Path to compression config YAML
        seed: Random seed for reproducibility
        batch_size: Number of entries per JSONL file during processing
        min_tokens: Minimum token count to filter trajectories (default: 16000)
        num_proc: Number of parallel workers for tokenization (default: 8)
        skip_download: Skip download and use existing sampled data
    """
    print("=" * 70)
    print("📊 轨迹抽样与压缩")
    print("=" * 70)
    
    # Parse datasets
    if datasets:
        dataset_list = [d.strip() for d in datasets.split(",")]
    else:
        dataset_list = DEFAULT_DATASETS
    
    print(f"\n📋 配置:")
    print(f"   总样本数: {total_samples:,}")
    print(f"   最低 token 过滤: {min_tokens:,}")
    print(f"   并行工作线程: {num_proc}")
    print(f"   数据集: {len(dataset_list)}")
    for ds in dataset_list:
        print(f"      - {ds}")
    print(f"   输出名称: {output_name}")
    print(f"   Config: {config}")
    print(f"   Seed: {seed}")
    
    # Setup paths
    base_dir = Path(__file__).parent.parent
    sampled_dir = base_dir / "data" / f"{output_name}_raw"
    compressed_dir = base_dir / "data" / f"{output_name}_batches"
    final_output = base_dir / "data" / f"{output_name}.jsonl"
    
    if not skip_download:
        # Step 1: Download, filter by token count, and sample from combined pool
        samples = sample_from_datasets(
            dataset_list, 
            total_samples, 
            min_tokens=min_tokens,
            seed=seed,
            num_proc=num_proc
        )
        
        if not samples:
            print("❌ 未收集到样本。退出。")
            return
        
        # Step 2: Save to JSONL files
        save_samples_for_compression(samples, sampled_dir, batch_size)
    else:
        print(f"\n⏭️  跳过下载，使用 {sampled_dir} 中的已有数据")
    
    # Step 3: Run compression
    config_path = base_dir / config
    if not config_path.exists():
        print(f"❌ 未找到配置: {config_path}")
        return
    
    run_compression(sampled_dir, compressed_dir, str(config_path))
    
    # Step 4: Merge into single JSONL file
    merge_output_to_single_jsonl(compressed_dir, final_output)
    
    print("\n" + "=" * 70)
    print("✅ 完成!")
    print("=" * 70)
    print(f"\n📁 原始样本:        {sampled_dir}")
    print(f"📁 压缩批次: {compressed_dir}")
    print(f"📁 最终输出:       {final_output}")
    print(f"\n上传到 HuggingFace:")
    print(f"   huggingface-cli upload NousResearch/{output_name} {final_output}")


if __name__ == "__main__":
    fire.Fire(main)
