"""The test harness releases dashboard copies, while keeping atomic rollback real."""

import pytest
from botocore.exceptions import ClientError


def test_moto_dashboard_copies_do_not_accumulate_or_weaken_rollback(dynamodb_table):
    from moto.core.model_instances import model_data

    table = dynamodb_table
    table.put_item(Item={"PK": "test", "SK": "state", "value": 1})
    for value in range(2, 5):
        table.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": table.name,
                        "Key": {"PK": "test", "SK": "state"},
                        "UpdateExpression": "SET #value = :v",
                        "ExpressionAttributeNames": {"#value": "value"},
                        "ExpressionAttributeValues": {":v": value},
                    }
                }
            ]
        )
    models = model_data.get("dynamodb", {})
    assert sum(len(model.instances_tracked) for model in models.values()) == 0
    with pytest.raises(ClientError):
        table.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Update": {
                        "TableName": table.name,
                        "Key": {"PK": "test", "SK": "state"},
                        "UpdateExpression": "SET #value = :v",
                        "ExpressionAttributeNames": {"#value": "value"},
                        "ExpressionAttributeValues": {":v": 9},
                    }
                },
                {
                    "ConditionCheck": {
                        "TableName": table.name,
                        "Key": {"PK": "test", "SK": "missing"},
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                },
            ]
        )
    assert table.get_item(Key={"PK": "test", "SK": "state"})["Item"]["value"] == 4
