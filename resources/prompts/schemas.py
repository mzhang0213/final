SCHEMAS: dict[str, dict] = {

    "extract": {
        "transactions": [
            {
                "date": "YYYY-MM-DD or partial date, or null if undeterminable",
                "company": "merchant or institution name, or null",
                "amount": "signed numeric string e.g. '-12.50' or '500.00', or null",
            }
        ]
    },
}