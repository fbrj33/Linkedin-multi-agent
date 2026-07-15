from datetime import date

from orchestrator.workflow import generate_and_send_monthly_plan


def main(month: str | None = None):
    target_month = month or date.today().strftime("%Y-%m")
    return generate_and_send_monthly_plan(target_month)


if __name__ == "__main__":
    main()
