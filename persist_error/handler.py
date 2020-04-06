import json
import os
import logging

from .sfn import get_execution_history, get_state_machine_input, find_root_failure_state
from .sns import send_notification
from .sqs import send_message


logger = logging.getLogger()
logger.setLevel(logging.DEBUG)


def lambda_handler(event, context):
    logger.info(f'Event: {event}')
    logger.info(f'Context: {context}')

    queue_url = os.getenv('AWS_SQS_QUEUE_URL')
    sns_arn = os.getenv('AWS_SNS_ARN')
    region = os.getenv('AWS_DEPLOYMENT_REGION')
    max_retries = int(os.getenv('MAX_RETRIES', 4))

    execution_arn = event['executionArn']
    exec_history = get_execution_history(
        execution_arn=execution_arn,
        region=region
    )
    logger.info(f'Execution History: {exec_history}')

    try:
        initial_input = get_state_machine_input(exec_history)
        failure_state = find_root_failure_state(exec_history)
    except:
        error_handler_msg = (
            f'Human intervention required for execution {execution_arn}. '
            'Unable to figure out what went wrong with this execution.'
        )
        send_notification(sns_arn, error_handler_msg)
        return  # drop out of the function
    else:
        logger.info(f'Initial input: {initial_input}')
        logger.info(f'Failure state: {failure_state}')

    try:
        if initial_input['resumeState'] == failure_state['resumeState']:
            failure_state['stepFunctionFails'] += 1  # increment number of failures
        else:
            failure_state['stepFunctionFails'] = 1  # reset if failing on a different state than before
    except KeyError:
        failure_state['stepFunctionFails'] = 1  # start incrementing failures if this is first one

    logger.info(f'Incremented failure state: {failure_state}')

    if failure_state['stepFunctionFails'] <= max_retries:
        # send a message to SQS to restart the step function
        send_message(
            queue_url=queue_url,
            message_body=json.dumps(failure_state),
            region=region
        )
        logger.info(f'Failed event sent to {queue_url}.')
    else:
        # abort retry attempts if there more failures than the set condition
        # send a message to SNS for human to deal with it
        failure_message = (
            f'Step function execution {execution_arn} has terminally failed. '
            f'This input has caused {max_retries + 1} failures: {failure_state}.\n'
            f'Please take a closer look at the underlying records and data.'
        )
        resp = send_notification(sns_arn, failure_message)
        logger.info(f'Input failed more than {max_retries} times: {failure_state}. Notification sent to SNS: {resp}.')
    return failure_state
