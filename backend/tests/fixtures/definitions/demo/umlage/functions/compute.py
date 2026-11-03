from umlage.settle import InputError, settle


def run(input: dict, settings: dict, rules: dict) -> dict:
    try:
        result, questions, assumptions, waiting = settle(input, rules, settings)
    except InputError as exc:
        return {"errors": exc.messages}
    return {"result": result, "questions": questions, "assumptions": assumptions, "waiting_for": waiting}
