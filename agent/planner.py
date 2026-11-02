"""
Planner: decompõe tarefa, executor cumpre, replaneja se falhar, reflexão final.
"""
import json
from agent.llm import chat
from agent.agent import run_agent, Budget

def plan(task: str, context: str = "") -> list[str]:
    r = chat([
        {"role": "system", "content":
         'Decomponha a tarefa em 2-6 passos verificáveis e atômicos. '
         'Responda SOMENTE JSON: {"steps":["passo 1", "passo 2", ...]}'},
        {"role": "user", "content": f"Tarefa: {task}\nContexto: {context}"}],
        response_format={"type": "json_object"})
    return json.loads(r.choices[0].message.content)["steps"]

def critique(task: str, result: str) -> dict:
    r = chat([
        {"role": "system", "content":
         'Avalie se o resultado cumpre a tarefa completamente. '
         'JSON: {"ok":true/false,"problemas":["..."]}'},
        {"role": "user", "content": f"Tarefa: {task}\nResultado: {result}"}],
        response_format={"type": "json_object"})
    return json.loads(r.choices[0].message.content)

def run_planned(task: str, max_replans: int = 2) -> str:
    steps, done, replans = plan(task), [], 0
    while steps:
        step = steps.pop(0)
        ctx = "\n".join(f"- {s}: {r[:300]}" for s, r in done)
        result, _ = run_agent(
            f"Passo atual: {step}\n\nProgresso até agora:\n{ctx}\n\nObjetivo geral: {task}",
            budget=Budget(steps=8)
        )
        if result.startswith("ABORTADO") and replans < max_replans:
            replans += 1
            steps = plan(task, context=f"Falhou em '{step}': {result}\nFeito: {ctx}")
            continue
        done.append((step, result))
    final = done[-1][1] if done else ""
    review = critique(task, final)
    if not review["ok"] and replans < max_replans:
        return run_planned(f"{task}\nCorrija estes problemas: {review['problemas']}", max_replans - 1)
    return final