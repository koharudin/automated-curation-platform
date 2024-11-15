import ast
import logging
import os
import platform
import shutil
import smtplib
import zipfile
import re
from tempfile import NamedTemporaryFile

import psutil
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Callable

import tomli
from hypothesis import settings
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor

import requests
from dynaconf import Dynaconf
from fastapi import HTTPException
from starlette import status

from src.dbz import DatabaseManager, DepositStatus
from src.models.bridge_output_model import BridgeOutputDataModel, TargetResponse

LOG_NAME_ACP = 'acp'
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["BASE_DIR"] = os.getenv("BASE_DIR", base_dir)
settings = Dynaconf(root_path=f'{base_dir}/conf', settings_files=["*.toml"],
                    environments=True)
data = {}

db_manager = DatabaseManager(db_dialect=settings.DB_DIALECT, db_url=settings.DB_URL, encryption_key=settings.DB_ENCRYPTION_KEY)

transformer_headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {settings.DANS_TRANSFORMER_SERVICE_API_KEY}'
}

transformer_headers_xml = {
    'Content-Type': 'application/xml',
    'Authorization': f'Bearer {settings.DANS_TRANSFORMER_SERVICE_API_KEY}'
}

assistant_repo_headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {settings.DANS_REPO_ASSISTANT_SERVICE_API_KEY}'
}

def get_version():
    with open(os.path.join(os.getenv("BASE_DIR"), 'pyproject.toml'), 'rb') as file:
        package_details = tomli.load(file)
    return package_details['tool']['poetry']['version']

def get_name():
    with open(os.path.join(os.getenv("BASE_DIR"), 'pyproject.toml'), 'rb') as file:
        package_details = tomli.load(file)
    return package_details['tool']['poetry']['name']

def setup_logger():
    """
    This function sets up the logger for the application.

    It iterates over the list of loggers specified in the settings, and for each logger, it:
    - Gets or creates a logger with the specified name.
    - Creates a formatter with the specified format.
    - Creates a file handler that writes to the specified log file in append mode, and sets its formatter.
    - Creates a stream handler (which writes to stdout by default) and sets its formatter.
    - Creates a timed rotating file handler that rotates the log file every 8 hours and keeps the last 10 log files, and adds it to the logger.
    - Sets the log level of the logger.
    - Adds the file handler and the stream handler to the logger.
    - Logs a startup message at the debug level, which includes the current time and the Python version.

    The logger settings (name, format, log file, and log level) are read from the `LOGGERS` setting in the application's configuration.

    The startup message is logged using the `logger` function defined elsewhere in this module.
    """
    now = datetime.utcnow()
    for log in settings.LOGGERS:
        log_setup = logging.getLogger(log.get('name'))
        formatter = logging.Formatter(log.get('log_format'))
        file_handler = logging.FileHandler(log.get('log_file'), mode='a')
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        rotating_handler = TimedRotatingFileHandler(log.get('log_file'), when="H", interval=8, backupCount=10)
        log_setup.addHandler(rotating_handler)
        log_setup.setLevel(log.get('log_level'))
        log_setup.addHandler(file_handler)
        log_setup.addHandler(stream_handler)
        logger(f"Start {log.get('name')} at {now} Pyton version: {platform.python_version()}",
               'debug', log.get('name'))


def logger(msg, level, logfile):
    log = logging.getLogger(logfile)
    if level == 'info': log.info(msg)
    if level == 'warning': log.warning(msg)
    if level == 'error': log.error(msg)
    if level == 'debug': log.debug(msg)


def get_class(kls) -> Any:
    """
    This function dynamically imports a class from a module.

    It takes a string `kls` as input, which should be the fully qualified name of a class (i.e., including its module path).
    The string is split into parts, and the module path is reconstructed by joining all parts except the last one.
    The module is then imported using the `__import__` function, and the class is retrieved using `getattr`.

    If the module cannot be found, a `ModuleNotFoundError` is caught and logged, and the function returns `None`.

    Parameters:
    kls (str): The fully qualified name of a class to import.

    Returns:
    Any: The class if it can be imported, or `None` otherwise.
    """
    parts = kls.split('.')
    module = ".".join(parts[:-1])
    try:
        m = __import__(module)
        for comp in parts[1:]:
            m = getattr(m, comp)
        return m
    except ModuleNotFoundError as e:
        print(f'error: {kls}')
        logger(f'ModuleNotFoundError: {e}', 'error', LOG_NAME_ACP)
    return None

def transform(transformer_url: str, str_tobe_transformed: str, headers: {} = None) -> str:
    logger(f'transformer_url: {transformer_url}', settings.LOG_LEVEL, LOG_NAME_ACP)
    if not isinstance(str_tobe_transformed, str):
        raise ValueError(f"Error - str_tobe_transformed is not a string. It is : {type(str_tobe_transformed)}")
    if headers is None:
        headers = transformer_headers

    response = requests.post(transformer_url, headers=headers, data=str_tobe_transformed)
    if response.status_code == 200:
        return response.json().get('result')

    logger(f'transformer_response.status_code: {response.status_code}', 'error', LOG_NAME_ACP)
    raise ValueError(f"Error - Transformer response status code: {response.status_code}")

def transform_json(transformer_url: str, str_tobe_transformed: str) -> str:
   transform(transformer_url, str_tobe_transformed, transformer_headers)



def transform_xml(transformer_url: str, str_tobe_transformed: str) -> str:
    transform(transformer_url, str_tobe_transformed, transformer_headers_xml)

# def transform(transformer_url: str, input: str) -> str:
#     logger(transformer_url: {transformer_url}', LOGGER_LEVEL_DEBUG, LOG_NAME_PS)
#     logger(f'input: {input}', LOGGER_LEVEL_DEBUG, LOG_NAME_PS)
#     try:
#         transformer_response = requests.post(transformer_url, headers=transformer_headers, data=input)
#         if transformer_response.status_code == 200:
#             transformed_metadata = transformer_response.json()
#             str_transformed_metadata = transformed_metadata.get('result')
#             logger(f'Transformer result: {str_transformed_metadata}', LOGGER_LEVEL_DEBUG, LOG_NAME_PS)
#             return str_transformed_metadata
#         logger(transformer_response.status_code: {transformer_response.status_code}', 'error', LOG_NAME_PS)
#         raise ValueError(f"Error - Transformer response status code: {transformer_response.status_code}")
#     except ConnectionError as ce:
#         logger(f'Errors during transformer: {ce.with_traceback(ce.__traceback__)}', LOGGER_LEVEL_DEBUG, LOG_NAME_PS)
#         raise ValueError(f"Error - {ce.with_traceback(ce.__traceback__)}")
#     except Exception as ex:
#         raise ValueError(f"Error - {ex.with_traceback(ex.__traceback__)}")


# def handle_deposit_exceptions(bridge_output_model: BridgeOutputModel) -> Callable[
#     [Any], Callable[[tuple[Any, ...], dict[str, Any]], BridgeOutputModel | Any]]:
#     def decorator(func):
#         @wraps(func)
#         def wrapper(*args, **kwargs):
#             try:
#                 print("start")
#                 print(f'kwargs: {kwargs}')
#                 print(f'args: {args}')
#                 # Call the original function
#                 rv = func(*args, **kwargs)
#                 print("end")
#                 return rv
#             except Exception as ex:
#                 # Handle the exception and provide the default response
#                 logger(f'Errors in {func.__name__}: {ex.with_traceback(ex.__traceback__)}',
#                        LOGGER_LEVEL_DEBUG, LOG_NAME_PS)
#                 bridge_output_model.deposit_status = DepositStatus.ERROR
#                 target_response = TargetResponse()
#                 target_response.duration=10100
#                 target_response.error="hello error"
#                 bridge_output_model.message = "this is bridge message"
#                 target_response.message = "TARGET MESSAGE"
#                 bridge_output_model.response = target_response
#                 return bridge_output_model
#
#         return wrapper
#
#     return decorator



def handle_deposit_exceptions(
        func) -> Callable[[tuple[Any, ...], dict[str, Any]], BridgeOutputDataModel | Any]:
    """
    This function is a decorator that wraps around a function to handle exceptions during the deposit process.

    It logs the entry into the function it is decorating, then attempts to execute the function.
    If an exception is raised during the execution of the function, it logs the error and creates a BridgeOutputDataModel
    instance with an error status and a TargetResponse instance containing the error details.

    The decorated function should take a BridgeOutputDataModel instance as its first argument.

    Parameters:
    func (Callable): The function to be decorated.

    Returns:
    Callable: The decorated function.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        #logger(f'Enter to handle_deposit_exceptions for {func.__name__}. args: {args}', settings.LOG_LEVEL, LOG_NAME_PS)
        try:
            rv = func(*args, **kwargs)
            return rv
        except Exception as ex:
            logger(f'Errors in {func.__name__}: {ex} - {ex.with_traceback(ex.__traceback__)}',
                   settings.LOG_LEVEL, LOG_NAME_ACP)
            target = args[0].target
            bom = BridgeOutputDataModel()
            bom.deposit_status = DepositStatus.ERROR
            tr = TargetResponse()
            tr.url = target.target_url
            tr.status = DepositStatus.ERROR
            tr.error = f'error: {ex.with_traceback(ex.__traceback__)}'
            tr.message = f"Error {func.__name__}. Causes: {ex.__class__.__name__} {ex}"
            bom.response = tr
            return bom

    return wrapper


def handle_ps_exceptions(func) -> Any:
    """
    This function is a decorator that wraps around a function to handle exceptions during the execution of the function.

    It logs the entry into the function it is decorating, then attempts to execute the function.
    If an HTTPException is raised during the execution of the function, it logs the error and re-raises the exception.
    If any other exception is raised, it sends an email with the error details, logs the error, and re-raises the exception.

    The decorated function can take any number of positional and keyword arguments.

    Parameters:
    func (Callable): The function to be decorated.

    Returns:
    Callable: The decorated function.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            logger(f'Enter to handle_ps_exceptions:: {func.__name__}', settings.LOG_LEVEL, LOG_NAME_ACP)
            rv = func(*args, **kwargs)
            return rv
        except HTTPException as ex:
            # send_mail(f'handle_ps_exceptions: Errors in {func.__name__}', f'status code: {ex.status_code}.'
            #                                                               f'\nDetails: {ex.detail}.')
            logger(
                f'handle_ps_exceptions: Errors in {func.__name__}. status code: {ex.status_code}. Details: {ex.detail}. '
                f'args: {args}', settings.LOG_LEVEL, LOG_NAME_ACP)
            raise ex
        except Exception as ex:
            send_mail(f'handle_ps_exceptions: Errors in {func.__name__}', f'{ex} - '
                                                                          f'{ex.with_traceback(ex.__traceback__)}.')
            logger(f'handle_ps_exceptions: Errors in {func.__name__}: {ex} - {ex.with_traceback(ex.__traceback__)}',
                   settings.LOG_LEVEL, LOG_NAME_ACP)
            raise ex
        except BaseException as ex:
            send_mail(f'handle_ps_exceptions: Errors in {func.__name__}', f'{ex} - '
                                                                          f'{ex.with_traceback(ex.__traceback__)}.')
            logger(f'handle_ps_exceptions: Errors in {func.__name__}:  {ex} - {ex.with_traceback(ex.__traceback__)}',
                   settings.LOG_LEVEL, LOG_NAME_ACP)
            raise ex

    return wrapper


def inspect_bridge_module(py_file_path: str):
    """
    This function inspects a Python module and returns a list of classes that inherit from the 'Bridge' class.

    It opens the Python file at the given path and parses it into an AST (Abstract Syntax Tree) using the `ast.parse` function.
    It then iterates over the nodes in the AST, and for each class definition, it checks if it inherits from the 'Bridge' class.
    If it does, it constructs the fully qualified name of the class and adds it to the results list.

    The fully qualified name of a class is constructed by replacing the base directory path in the file path with an empty string,
    replacing all slashes with dots, and appending the class name.

    Parameters:
    py_file_path (str): The path to the Python file to inspect.

    Returns:
    list[dict[str, str]]: A list of dictionaries, where each dictionary has one key-value pair.
                           The key is the name of a class that inherits from the 'Bridge' class,
                           and the value is the fully qualified name of the class.
    """
    with open(py_file_path, 'r') as f:
        bridge_mdl = ast.parse(f.read())
    results = []
    for node in bridge_mdl.body:
        if isinstance(node, ast.ClassDef) and any(
                isinstance(base, ast.Name) and base.id == 'Bridge' for base in node.bases):
            module_name = py_file_path.replace(f'{os.getenv("BASE_DIR", os.getcwd())}/', '').replace('/', '.')
            name_of_bridge_subclass = f"{module_name[:-3]}.{node.name}"
            results.append({node.name: name_of_bridge_subclass})
    return results


# class ACPeException(Exception):
#     def __init__(self, bom: BridgeOutputDataModel, message: str):
#         self.bom = bom
#         self.message = message
#         super().__init__(self.message)

def send_mail(subject: str, text: str):
    """
    Send an email with the specified subject and text.

    This function sends an email using the SMTP protocol. The email is sent from the sender's email address
    to the recipient's email address, with the specified subject and text. The email settings (sender email,
    app password, recipient email, and mail subject prefix) are read from the application's configuration.

    Args:
        subject (str): The subject of the email.
        text (str): The text content of the email.

    Raises:
        Exception: If there is an error sending the email.
    """
    sender_email = settings.MAIL_USR
    app_password = settings.MAIL_PASS
    recipient_email = settings.MAIL_TO
    message = MIMEMultipart()
    message['From'] = sender_email
    message['To'] = recipient_email
    message['Subject'] = f'{settings.get("MAIL_SUBJECT_PREFIX", "mail_subject_prefix not set")}: {subject}'
    message.attach(MIMEText(text, 'plain'))

    if settings.get('send_mail', True):
        try:
            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(sender_email, app_password)
                server.sendmail(sender_email, recipient_email, message.as_string())
            logger(f"Email sent successfully to {recipient_email}", "debug", LOG_NAME_ACP)
        except Exception as e:
            print(f"Error: {e}")
            logger(f"Unsuccessful sent email to {recipient_email}", "error", LOG_NAME_ACP)
    else:
        logger("Sending email is disabled.", settings.LOG_LEVEL, LOG_NAME_ACP)


def dmz_dataverse_headers(username, password) -> dict:
    headers = {'X-Authorization': settings.dmz_x_authorization_value} if settings.exists("dmz_x_authorization_value",
                                                                                         fresh=False) else {}
    if username == 'API_KEY':
        headers["X-Dataverse-key"] = password
    return headers


def upload_large_file(url, file_path, json_data, api_key, file_name=None):
    def create_callback(encoder):
        encoder_len = encoder.len
        last_reported_progress = -5  # Initialize to -5 so it prints at 0%

        def callback(monitor):
            nonlocal last_reported_progress  # To modify the outer variable
            progress = (monitor.bytes_read / encoder_len) * 100
            if progress >= last_reported_progress + 5 or progress > 95:
                memory_usage_msg = f", Memory usage: {psutil.Process().memory_info().rss / (1024 * 1024):.2f} MB" \
                    if progress >= last_reported_progress + 5 else ""
                logger(f"Upload Progress: {progress:.2f}%{memory_usage_msg}", settings.LOG_LEVEL, LOG_NAME_ACP)
                last_reported_progress = progress if progress >= last_reported_progress + 5 else last_reported_progress

        return callback

    with open(file_path, 'rb') as f:
        encoder = MultipartEncoder(
            fields={'file': (file_name if file_name else os.path.basename(file_path), f, 'application/octet-stream'),
                    'jsonData': (None, json_data['jsonData'])}
        )
        callback = create_callback(encoder)
        monitor = MultipartEncoderMonitor(encoder, callback)
        logger(f'upload_large_file  api_key: {api_key}', settings.LOG_LEVEL, LOG_NAME_ACP)
        response = requests.post(url, data=monitor, headers={"X-Dataverse-key": api_key,
                                                             'X-Authorization': settings.dmz_x_authorization_value,
                                                             'Content-Type': monitor.content_type})

        logger(f'upload_large_file response: {response.status_code}', settings.LOG_LEVEL, LOG_NAME_ACP)
        if response.status_code == status.HTTP_502_BAD_GATEWAY:
            logger(f'ERROR 502 upload_large_file response: {response.text}', 'error', LOG_NAME_ACP)

    return response


def zip_with_progress(file_path, zip_path):
    # Resolve the file_path if it's a symlink
    if os.path.islink(file_path):
        real_file_path = os.readlink(file_path)
        print(f"'{file_path}' is a symlink, including the real file '{real_file_path}'.")
    else:
        real_file_path = file_path

    file_size = os.path.getsize(real_file_path)
    chunk_size = 10 * 1024 * 1024  # 10MB chunks
    processed_size = 0
    last_printed_progress = 0
    arcname = os.path.basename(file_path)

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        with open(real_file_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break

                # Create a temporary file to write the chunk
                temp_chunk_path = 'temp_chunk'
                with open(temp_chunk_path, 'wb') as tempf:
                    tempf.write(chunk)

                zipf.write(temp_chunk_path, arcname=arcname)

                processed_size += len(chunk)
                progress = processed_size / file_size * 100
                if progress - last_printed_progress >= 10:
                    logger(f"Zipping Progress of {arcname}: {progress:.0f}%", settings.LOG_LEVEL, LOG_NAME_ACP)
                    last_printed_progress += 10

                # Remove the temporary file
                os.remove(temp_chunk_path)

    logger(f"Zipping completed.", settings.LOG_LEVEL, LOG_NAME_ACP)


def delete_symlink_and_target(link_name):
    if os.path.islink(link_name):
        target = os.readlink(link_name)
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        os.remove(link_name)
        logger(f'{link_name} and its target {target} DELETED successfully.', settings.LOG_LEVEL, LOG_NAME_ACP)


def compress_zip_file(original_zip_path):
    if not os.path.exists(original_zip_path):
        print(f"File {original_zip_path} does not exist.")
        return

    with NamedTemporaryFile(delete=False) as temp_file:
        temp_file_path = temp_file.name

    try:
        with zipfile.ZipFile(original_zip_path, 'r') as original_zip:
            total_size = sum([zinfo.file_size for zinfo in original_zip.infolist()])
            processed_size = 0

            with zipfile.ZipFile(temp_file_path, 'w', compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=9) as compressed_zip:
                for file_info in original_zip.infolist():
                    with original_zip.open(file_info.filename) as file:
                        file_content = file.read()
                        compressed_zip.writestr(file_info, file_content)
                        processed_size += file_info.file_size
                        progress = (processed_size / total_size) * 100
                        print(f"Progress: {progress:.2f}%")

        shutil.move(temp_file_path, original_zip_path)
        print(f"Compression of {original_zip_path} completed successfully.")
    except Exception as e:
        os.remove(temp_file_path)
        print(f"An error occurred: {e}")


def zip_a_zipfile_with_progress(original_zip_path, new_zip_path):
    # Get the size of the original zip file
    original_zip_size = os.path.getsize(original_zip_path)
    arcname = original_zip_path.split('/')[-1]
    # Create a new zip file (outer zip)
    with zipfile.ZipFile(new_zip_path, 'w', zipfile.ZIP_DEFLATED) as new_zip:
        # Add the original zip file to the new zip file
        new_zip.write(original_zip_path, arcname=arcname)

        # Calculate the progress (since we're adding the file in one go, it'll jump to 100%)
        progress = 100  # In a real-world scenario, you'd calculate this based on bytes written vs total size

        # Print the progress
        logger(f"Zipping Progress of {arcname} : {progress}%", settings.LOG_LEVEL, LOG_NAME_ACP)





def escape_invalid_json_characters(json_string: str) -> str:
    # Replace invalid control characters with their escaped equivalents
    escaped_string = re.sub(r'[\x00-\x1F\x7F]', lambda match: '\\u{:04x}'.format(ord(match.group())), json_string)
    return escaped_string


