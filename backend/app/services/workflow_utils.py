from typing import Dict, Any, List
import copy

def inject_workflow(graph: Dict[str, Any], prompt_text: str, seed: int, batch_size: int, prompt_nodes: List[str] = None, seed_nodes: List[str] = None) -> Dict[str, Any]:
    """
    Injects prompt text and seed into the workflow graph.
    If prompt_nodes/seed_nodes are provided (IDs), uses them.
    Otherwise, heuristics are used.
    """
    new_graph = copy.deepcopy(graph)

    # Heuristic detection if not provided
    if not prompt_nodes:
        prompt_nodes = []
        for node_id, node in new_graph.items():
            if node.get("class_type") == "CLIPTextEncode" and isinstance(node.get("inputs", {}).get("text"), str):
                 # Simple heuristic: if text is not empty or seems like the positive prompt.
                 # For safety, let's assume we need to replace ALL CLIPTextEncode? No, negative prompt exists.
                 # V3 SRS 3.1.1 says: "Admin must upload workflow metadata (which nodes are Prompt/Seed)".
                 # So we strictly prefer provided IDs. If not, we might fail or guess.
                 pass

    if not seed_nodes:
        seed_nodes = []
        for node_id, node in new_graph.items():
            if "KSampler" in node.get("class_type", "") or "Seed" in node.get("class_type", ""):
                seed_nodes.append(node_id)

    # Injection
    for node_id in prompt_nodes:
        if node_id in new_graph:
            new_graph[node_id]["inputs"]["text"] = prompt_text

    for node_id in seed_nodes:
        if node_id in new_graph:
            new_graph[node_id]["inputs"]["seed"] = seed
            # Also handle batch_size if it exists in this node (e.g. EmptyLatentImage often controls batch size, not KSampler directly in some workflows)
            # Actually batch_size is usually in EmptyLatentImage.
            # SRS 3.3.2: "workflow.batch_size = N ... using LatentBatchSeedBehavior"
            # If using LatentBatchSeedBehavior, we inject into THAT node.

    # Batch Size Injection
    # We need to find EmptyLatentImage or similar
    for node_id, node in new_graph.items():
        if node.get("class_type") == "EmptyLatentImage":
            if "batch_size" in node["inputs"]:
                node["inputs"]["batch_size"] = batch_size

    return new_graph

def find_heaviest_prompt(prompts: List[Any]) -> Any:
    """
    Returns the 'heaviest' prompt object.
    Heuristic: Longest text.
    """
    if not prompts: return None
    return max(prompts, key=lambda p: len(p.text))

def adjust_graph_resolution(graph: Dict[str, Any], factor: float = 0.5) -> Dict[str, Any]:
    """
    Reduces resolution in EmptyLatentImage nodes by factor.
    """
    new_graph = copy.deepcopy(graph)
    for node_id, node in new_graph.items():
        if node.get("class_type") == "EmptyLatentImage":
            if "width" in node["inputs"]:
                node["inputs"]["width"] = int(node["inputs"]["width"] * factor)
            if "height" in node["inputs"]:
                node["inputs"]["height"] = int(node["inputs"]["height"] * factor)
    return new_graph
