from umlage.settle import InputError, settle


def run(input: dict, settings: dict, rules: dict) -> dict:
    try:
        result, questions, assumptions, waiting, decide = settle(input, rules, settings)
    except InputError as exc:
        return {"errors": exc.messages}
    if decide:
        return {"result": None, "decide": decide}
    return {"result": result, "questions": questions, "assumptions": assumptions, "waiting_for": waiting}
