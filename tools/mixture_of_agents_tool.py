#!/usr/bin/env python3
"""
混合代理工具模块

本模块实现了混合代理（MoA）方法论，通过分层架构利用多个 LLM 的
集体优势，在复杂推理任务上实现最先进的性能。

基于研究论文："Mixture-of-Agents Enhances Large Language Model Capabilities"
作者：Junlin Wang 等（arXiv:2406.04692v1）

关键特性：
- 多层 LLM 协作以增强推理能力
- 参考模型并行处理以提高效率
- 智能聚合和综合多样化响应
- 专为需要深度推理的极困难问题而设计
- 针对编码、数学和复杂分析任务优化

可用工具：
- mixture_of_agents_tool：使用多个前沿模型处理复杂查询

架构：
1. 参考模型并行生成多样化的初始响应
2. 聚合器模型将响应综合为高质量输出
3. 可以使用多层进行迭代细化（未来增强）

使用的模型（通过 OpenRouter）：
- 参考模型：claude-opus-4.6、gemini-3-pro-preview、gpt-5.4-pro、deepseek-v3.2
- 聚合器模型：claude-opus-4.6（用于综合的最高能力）

配置：
    要自定义 MoA 设置，请修改本文件顶部的配置常量：
    - REFERENCE_MODELS：用于生成分多样化初始响应的模型列表
    - AGGREGATOR_MODEL：用于综合最终响应的模型
    - REFERENCE_TEMPERATURE/AGGREGATOR_TEMPERATURE：采样温度
    - MIN_SUCCESSFUL_REFERENCES：继续所需的最小成功模型数

用法：
    from mixture_of_agents_tool import mixture_of_agents_tool
    import asyncio

    # 处理复杂查询
    result = await mixture_of_agents_tool(
        user_prompt="Solve this complex mathematical proof..."
    )
"""

import json
import logging
import os
import asyncio
import datetime
from typing import Dict, Any, List, Optional
from tools.openrouter_client import get_async_client as _get_openrouter_client, check_api_key as check_openrouter_api_key
from agent.auxiliary_client import extract_content_or_reasoning
from tools.debug_helpers import DebugSession

logger = logging.getLogger(__name__)

# MoA 处理配置
# 参考模型——这些并行生成多样化的初始响应。
# 保持此列表与当前顶级 OpenRouter 前沿选项一致。
REFERENCE_MODELS = [
    "anthropic/claude-opus-4.6",
    "google/gemini-3-pro-preview",
    "openai/gpt-5.4-pro",
    "deepseek/deepseek-v3.2",
]

# 聚合器模型——将参考响应综合为最终输出。
# 优先选择当前 OpenRouter 阵容中最强的综合模型。
AGGREGATOR_MODEL = "anthropic/claude-opus-4.6"

# 针对 MoA 性能优化的温度设置
REFERENCE_TEMPERATURE = 0.6  # 为多样化视角平衡创造力
AGGREGATOR_TEMPERATURE = 0.4  # 为一致性专注综合

# 故障处理配置
MIN_SUCCESSFUL_REFERENCES = 1  # 继续所需的最小成功参考模型数

# System prompt for the aggregator model (from the research paper)
AGGREGATOR_SYSTEM_PROMPT = """You have been provided with a set of responses from various open-source models to the latest user query. Your task is to synthesize these responses into a single, high-quality response. It is crucial to critically evaluate the information provided in these responses, recognizing that some of it may be biased or incorrect. Your response should not simply replicate the given answers but should offer a refined, accurate, and comprehensive reply to the instruction. Ensure your response is well-structured, coherent, and adheres to the highest standards of accuracy and reliability.

Responses from models:"""

_debug = DebugSession("moa_tools", env_var="MOA_TOOLS_DEBUG")


def _construct_aggregator_prompt(system_prompt: str, responses: List[str]) -> str:
    """
    构造包含所有模型响应的聚合器最终系统提示词。

    参数：
        system_prompt (str)：聚合的基础系统提示词
        responses (List[str])：参考模型响应列表

    返回：
        str：带有编号响应的完整系统提示词
    """
    response_text = "\n".join([f"{i+1}. {response}" for i, response in enumerate(responses)])
    return f"{system_prompt}\n\n{response_text}"


async def _run_reference_model_safe(
    model: str,
    user_prompt: str,
    temperature: float = REFERENCE_TEMPERATURE,
    max_tokens: int = 32000,
    max_retries: int = 6
) -> tuple[str, str, bool]:
    """
    使用重试逻辑和优雅故障处理运行单个参考模型。

    参数：
        model (str)：要使用的模型标识符
        user_prompt (str)：用户的查询
        temperature (float)：响应生成的采样温度
        max_tokens (int)：响应中的最大 token 数
        max_retries (int)：最大重试次数

    返回：
        tuple[str, str, bool]：(model_name, response_content_or_error, success_flag)
    """
    for attempt in range(max_retries):
        try:
            logger.info("Querying %s (attempt %s/%s)", model, attempt + 1, max_retries)
            
            # Build parameters for the API call
            api_params = {
                "model": model,
                "messages": [{"role": "user", "content": user_prompt}],
                "extra_body": {
                    "reasoning": {
                        "enabled": True,
                        "effort": "xhigh"
                    }
                }
            }
            
            # GPT models (especially gpt-4o-mini) don't support custom temperature values
            # Only include temperature for non-GPT models
            if not model.lower().startswith('gpt-'):
                api_params["temperature"] = temperature
            
            response = await _get_openrouter_client().chat.completions.create(**api_params)
            
            content = extract_content_or_reasoning(response)
            if not content:
                # Reasoning-only response — let the retry loop handle it
                logger.warning("%s returned empty content (attempt %s/%s), retrying", model, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(min(2 ** (attempt + 1), 60))
                    continue
            logger.info("%s responded (%s characters)", model, len(content))
            return model, content, True
            
        except Exception as e:
            error_str = str(e)
            # Keep retry-path logging concise; full tracebacks are reserved for
            # terminal failure paths so long-running MoA retries don't flood logs.
            if "invalid" in error_str.lower():
                logger.warning("%s invalid request error (attempt %s): %s", model, attempt + 1, error_str)
            elif "rate" in error_str.lower() or "limit" in error_str.lower():
                logger.warning("%s rate limit error (attempt %s): %s", model, attempt + 1, error_str)
            else:
                logger.warning("%s unknown error (attempt %s): %s", model, attempt + 1, error_str)

            if attempt < max_retries - 1:
                # Exponential backoff for rate limiting: 2s, 4s, 8s, 16s, 32s, 60s
                sleep_time = min(2 ** (attempt + 1), 60)
                logger.info("Retrying in %ss...", sleep_time)
                await asyncio.sleep(sleep_time)
            else:
                error_msg = f"{model} failed after {max_retries} attempts: {error_str}"
                logger.error("%s", error_msg, exc_info=True)
                return model, error_msg, False


async def _run_aggregator_model(
    system_prompt: str,
    user_prompt: str,
    temperature: float = AGGREGATOR_TEMPERATURE,
    max_tokens: int = None
) -> str:
    """
    运行聚合器模型以综合最终响应。

    参数：
        system_prompt (str)：包含所有参考响应的系统提示词
        user_prompt (str)：原始用户查询
        temperature (float)：专注的温度以获得一致的聚合
        max_tokens (int)：最终响应中的最大 token 数

    返回：
        str：综合的最终响应
    """
    logger.info("Running aggregator model: %s", AGGREGATOR_MODEL)

    # Build parameters for the API call
    api_params = {
        "model": AGGREGATOR_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "extra_body": {
            "reasoning": {
                "enabled": True,
                "effort": "xhigh"
            }
        }
    }

    # GPT models (especially gpt-4o-mini) don't support custom temperature values
    # Only include temperature for non-GPT models
    if not AGGREGATOR_MODEL.lower().startswith('gpt-'):
        api_params["temperature"] = temperature

    response = await _get_openrouter_client().chat.completions.create(**api_params)

    content = extract_content_or_reasoning(response)

    # Retry once on empty content (reasoning-only response)
    if not content:
        logger.warning("Aggregator returned empty content, retrying once")
        response = await _get_openrouter_client().chat.completions.create(**api_params)
        content = extract_content_or_reasoning(response)

    logger.info("Aggregation complete (%s characters)", len(content))
    return content


async def mixture_of_agents_tool(
    user_prompt: str,
    reference_models: Optional[List[str]] = None,
    aggregator_model: Optional[str] = None
) -> str:
    """
    使用混合代理方法处理复杂查询。

    此工具利用多个前沿语言模型协作解决需要深度推理的极困难问题。它特别适用于：
    - 复杂的数学证明和计算
    - 高级编程问题和算法设计
    - 多步分析推理任务
    - 需要多样化领域专业知识的问题
    - 单个模型表现出局限性的任务

    MoA 方法使用固定的 2 层架构：
    1. 第一层：多个参考模型并行生成多样化响应（temp=0.6）
    2. 第二层：聚合器模型将最佳元素综合到最终响应中（temp=0.4）

    参数：
        user_prompt (str)：要解决的复杂查询或问题
        reference_models (Optional[List[str]])：要使用的自定义参考模型
        aggregator_model (Optional[str])：要使用的自定义聚合器模型

    返回：
        str：包含 MoA 结果的 JSON 字符串，结构如下：
             {
                 "success": bool,
                 "response": str,
                 "models_used": {
                     "reference_models": List[str],
                     "aggregator_model": str
                 },
                 "processing_time": float
             }

    引发：
        Exception：如果 MoA 处理失败或 API 密钥未设置
    """
    start_time = datetime.datetime.now()
    
    debug_call_data = {
        "parameters": {
            "user_prompt": user_prompt[:200] + "..." if len(user_prompt) > 200 else user_prompt,
            "reference_models": reference_models or REFERENCE_MODELS,
            "aggregator_model": aggregator_model or AGGREGATOR_MODEL,
            "reference_temperature": REFERENCE_TEMPERATURE,
            "aggregator_temperature": AGGREGATOR_TEMPERATURE,
            "min_successful_references": MIN_SUCCESSFUL_REFERENCES
        },
        "error": None,
        "success": False,
        "reference_responses_count": 0,
        "failed_models_count": 0,
        "failed_models": [],
        "final_response_length": 0,
        "processing_time_seconds": 0,
        "models_used": {}
    }
    
    try:
        logger.info("Starting Mixture-of-Agents processing...")
        logger.info("Query: %s", user_prompt[:100])
        
        # Validate API key availability
        if not os.getenv("OPENROUTER_API_KEY"):
            raise ValueError("OPENROUTER_API_KEY environment variable not set")
        
        # Use provided models or defaults
        ref_models = reference_models or REFERENCE_MODELS
        agg_model = aggregator_model or AGGREGATOR_MODEL
        
        logger.info("Using %s reference models in 2-layer MoA architecture", len(ref_models))
        
        # Layer 1: Generate diverse responses from reference models (with failure handling)
        logger.info("Layer 1: Generating reference responses...")
        model_results = await asyncio.gather(*[
            _run_reference_model_safe(model, user_prompt, REFERENCE_TEMPERATURE)
            for model in ref_models
        ])
        
        # Separate successful and failed responses
        successful_responses = []
        failed_models = []
        
        for model_name, content, success in model_results:
            if success:
                successful_responses.append(content)
            else:
                failed_models.append(model_name)
        
        successful_count = len(successful_responses)
        failed_count = len(failed_models)
        
        logger.info("Reference model results: %s successful, %s failed", successful_count, failed_count)
        
        if failed_models:
            logger.warning("Failed models: %s", ', '.join(failed_models))
        
        # Check if we have enough successful responses to proceed
        if successful_count < MIN_SUCCESSFUL_REFERENCES:
            raise ValueError(f"Insufficient successful reference models ({successful_count}/{len(ref_models)}). Need at least {MIN_SUCCESSFUL_REFERENCES} successful responses.")
        
        debug_call_data["reference_responses_count"] = successful_count
        debug_call_data["failed_models_count"] = failed_count
        debug_call_data["failed_models"] = failed_models
        
        # Layer 2: Aggregate responses using the aggregator model
        logger.info("Layer 2: Synthesizing final response...")
        aggregator_system_prompt = _construct_aggregator_prompt(
            AGGREGATOR_SYSTEM_PROMPT, 
            successful_responses
        )
        
        final_response = await _run_aggregator_model(
            aggregator_system_prompt,
            user_prompt,
            AGGREGATOR_TEMPERATURE
        )
        
        # Calculate processing time
        end_time = datetime.datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        logger.info("MoA processing completed in %.2f seconds", processing_time)
        
        # Prepare successful response (only final aggregated result, minimal fields)
        result = {
            "success": True,
            "response": final_response,
            "models_used": {
                "reference_models": ref_models,
                "aggregator_model": agg_model
            }
        }
        
        debug_call_data["success"] = True
        debug_call_data["final_response_length"] = len(final_response)
        debug_call_data["processing_time_seconds"] = processing_time
        debug_call_data["models_used"] = result["models_used"]
        
        # Log debug information
        _debug.log_call("mixture_of_agents_tool", debug_call_data)
        _debug.save()
        
        return json.dumps(result, indent=2, ensure_ascii=False)
        
    except Exception as e:
        error_msg = f"Error in MoA processing: {str(e)}"
        logger.error("%s", error_msg, exc_info=True)
        
        # Calculate processing time even for errors
        end_time = datetime.datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        # Prepare error response (minimal fields)
        result = {
            "success": False,
            "response": "MoA processing failed. Please try again or use a single model for this query.",
            "models_used": {
                "reference_models": reference_models or REFERENCE_MODELS,
                "aggregator_model": aggregator_model or AGGREGATOR_MODEL
            },
            "error": error_msg
        }
        
        debug_call_data["error"] = error_msg
        debug_call_data["processing_time_seconds"] = processing_time
        _debug.log_call("mixture_of_agents_tool", debug_call_data)
        _debug.save()
        
        return json.dumps(result, indent=2, ensure_ascii=False)


def check_moa_requirements() -> bool:
    """
    检查 MoA 工具的所有要求是否满足。

    返回：
        bool：如果满足要求则为 True，否则为 False
    """
    return check_openrouter_api_key()


def get_debug_session_info() -> Dict[str, Any]:
    """
    获取有关当前调试会话的信息。

    返回：
        Dict[str, Any]：包含调试会话信息的字典
    """
    return _debug.get_session_info()


def get_available_models() -> Dict[str, List[str]]:
    """
    获取有关 MoA 处理可用模型的信息。

    返回：
        Dict[str, List[str]]：包含参考模型和聚合器模型的字典
    """
    return {
        "reference_models": REFERENCE_MODELS,
        "aggregator_models": [AGGREGATOR_MODEL],
        "supported_models": REFERENCE_MODELS + [AGGREGATOR_MODEL]
    }


def get_moa_configuration() -> Dict[str, Any]:
    """
    获取当前的 MoA 配置设置。

    返回：
        Dict[str, Any]：包含所有配置参数的字典
    """
    return {
        "reference_models": REFERENCE_MODELS,
        "aggregator_model": AGGREGATOR_MODEL,
        "reference_temperature": REFERENCE_TEMPERATURE,
        "aggregator_temperature": AGGREGATOR_TEMPERATURE,
        "min_successful_references": MIN_SUCCESSFUL_REFERENCES,
        "total_reference_models": len(REFERENCE_MODELS),
        "failure_tolerance": f"{len(REFERENCE_MODELS) - MIN_SUCCESSFUL_REFERENCES}/{len(REFERENCE_MODELS)} models can fail"
    }


if __name__ == "__main__":
    """
    Simple test/demo when run directly
    """
    print("🤖 Mixture-of-Agents Tool Module")
    print("=" * 50)
    
    # Check if API key is available
    api_available = check_openrouter_api_key()
    
    if not api_available:
        print("❌ OPENROUTER_API_KEY environment variable not set")
        print("Please set your API key: export OPENROUTER_API_KEY='your-key-here'")
        print("Get API key at: https://openrouter.ai/")
        exit(1)
    else:
        print("✅ OpenRouter API key found")
    
    print("🛠️  MoA tools ready for use!")
    
    # Show current configuration
    config = get_moa_configuration()
    print("\n⚙️  Current Configuration:")
    print(f"  🤖 Reference models ({len(config['reference_models'])}): {', '.join(config['reference_models'])}")
    print(f"  🧠 Aggregator model: {config['aggregator_model']}")
    print(f"  🌡️  Reference temperature: {config['reference_temperature']}")
    print(f"  🌡️  Aggregator temperature: {config['aggregator_temperature']}")
    print(f"  🛡️  Failure tolerance: {config['failure_tolerance']}")
    print(f"  📊 Minimum successful models: {config['min_successful_references']}")
    
    # Show debug mode status
    if _debug.active:
        print(f"\n🐛 Debug mode ENABLED - Session ID: {_debug.session_id}")
        print(f"   Debug logs will be saved to: ./logs/moa_tools_debug_{_debug.session_id}.json")
    else:
        print("\n🐛 Debug mode disabled (set MOA_TOOLS_DEBUG=true to enable)")
    
    print("\nBasic usage:")
    print("  from mixture_of_agents_tool import mixture_of_agents_tool")
    print("  import asyncio")
    print("")
    print("  async def main():")
    print("      result = await mixture_of_agents_tool(")
    print("          user_prompt='Solve this complex mathematical proof...'")
    print("      )")
    print("      print(result)")
    print("  asyncio.run(main())")
    
    print("\nBest use cases:")
    print("  - Complex mathematical proofs and calculations")
    print("  - Advanced coding problems and algorithm design")
    print("  - Multi-step analytical reasoning tasks")
    print("  - Problems requiring diverse domain expertise")
    print("  - Tasks where single models show limitations")
    
    print("\nPerformance characteristics:")
    print("  - Higher latency due to multiple model calls")
    print("  - Significantly improved quality for complex tasks")
    print("  - Parallel processing for efficiency")
    print(f"  - Optimized temperatures: {REFERENCE_TEMPERATURE} for reference models, {AGGREGATOR_TEMPERATURE} for aggregation")
    print("  - Token-efficient: only returns final aggregated response")
    print("  - Resilient: continues with partial model failures")
    print("  - Configurable: easy to modify models and settings at top of file")
    print("  - State-of-the-art results on challenging benchmarks")
    
    print("\nDebug mode:")
    print("  # Enable debug logging")
    print("  export MOA_TOOLS_DEBUG=true")
    print("  # Debug logs capture all MoA processing steps and metrics")
    print("  # Logs saved to: ./logs/moa_tools_debug_UUID.json")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
from tools.registry import registry

MOA_SCHEMA = {
    "name": "mixture_of_agents",
    "description": "Route a hard problem through multiple frontier LLMs collaboratively. Makes 5 API calls (4 reference models + 1 aggregator) with maximum reasoning effort — use sparingly for genuinely difficult problems. Best for: complex math, advanced algorithms, multi-step analytical reasoning, problems benefiting from diverse perspectives.",
    "parameters": {
        "type": "object",
        "properties": {
            "user_prompt": {
                "type": "string",
                "description": "The complex query or problem to solve using multiple AI models. Should be a challenging problem that benefits from diverse perspectives and collaborative reasoning."
            }
        },
        "required": ["user_prompt"]
    }
}

registry.register(
    name="mixture_of_agents",
    toolset="moa",
    schema=MOA_SCHEMA,
    handler=lambda args, **kw: mixture_of_agents_tool(user_prompt=args.get("user_prompt", "")),
    check_fn=check_moa_requirements,
    requires_env=["OPENROUTER_API_KEY"],
    is_async=True,
    emoji="🧠",
)
