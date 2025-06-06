import dataclasses
import json
import urllib
import uuid
from typing import Optional

import pyiceberg.catalog
import pyiceberg.catalog.rest
import pyiceberg.typedef
import pytest
import requests

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr


class Secret(SecretStr, str):
    def __repr__(self):
        return "********"

    def __str__(self):
        return self.get_secret_value()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="lakekeeper_test__")
    management_url: str = Field()
    catalog_url: str = Field()
    aws_s3_access_key: Optional[Secret] = None
    aws_s3_secret_access_key: Optional[Secret] = None
    aws_s3_bucket: Optional[str] = None
    aws_s3_region: Optional[str] = None
    aws_s3_sts_role_arn: Optional[str] = None
    aws_s3_key_prefix: Optional[str] = None
    aws_s3_use_system_identity: Optional[bool] = None
    aws_s3_assume_role_arn: Optional[str] = None
    aws_s3_assume_role_external_id: Optional[str] = None
    s3_access_key: Optional[Secret] = None
    s3_secret_key: Optional[Secret] = None
    s3_bucket: Optional[str] = None
    s3_endpoint: Optional[str] = None
    s3_region: Optional[str] = None
    s3_path_style_access: Optional[str] = None
    s3_sts_mode: Optional[str] = None
    s3_allow_alternative_protocols: Optional[bool] = None
    azure_client_id: Optional[Secret] = None
    azure_client_secret: Optional[Secret] = None
    azure_tenant_id: Optional[Secret] = None
    azure_storage_account_name: Optional[str] = None
    azure_storage_filesystem: Optional[str] = None
    azure_allow_alternative_protocols: Optional[bool] = None
    gcs_credential: Optional[Secret] = None
    gcs_bucket: Optional[str] = None
    openid_provider_uri: Optional[str] = None
    openid_client_id: Optional[Secret] = None
    openid_client_secret: Optional[Secret] = None
    openid_scope: Optional[str] = "lakekeeper"
    trino_uri: Optional[str] = None
    trino_auth_enabled: Optional[str] = None
    lakekeeper_warehouse: Optional[str] = None
    spark_iceberg_version: str = "1.5.2"
    starrocks_uri: Optional[str] = None
    use_default_project: Optional[str] = None

    @property
    def token_endpoint(self) -> str:
        return f"{self.openid_provider_uri.rstrip('/')}/protocol/openid-connect/token"


settings = Settings()

STORAGE_CONFIGS = []

if settings.s3_access_key is not None:
    if settings.s3_sts_mode == "both":
        STORAGE_CONFIGS.append({"type": "s3", "sts-enabled": True})
        STORAGE_CONFIGS.append({"type": "s3", "sts-enabled": False})
    elif settings.s3_sts_mode == "enabled":
        STORAGE_CONFIGS.append({"type": "s3", "sts-enabled": True})
    elif settings.s3_sts_mode == "disabled":
        STORAGE_CONFIGS.append({"type": "s3", "sts-enabled": False})
    else:
        raise ValueError(
            f"Invalid LAKEKEEPER_TEST__S3_STS_MODE: {settings.s3_sts_mode}. "
            "must be one of 'both', 'enabled', 'disabled'"
        )

if (
    settings.aws_s3_access_key is not None and settings.aws_s3_access_key != ""
) or settings.aws_s3_use_system_identity:
    if settings.s3_sts_mode == "both":
        STORAGE_CONFIGS.append({"type": "aws", "sts-enabled": True})
        STORAGE_CONFIGS.append({"type": "aws", "sts-enabled": False})
    elif settings.s3_sts_mode == "enabled":
        STORAGE_CONFIGS.append({"type": "aws", "sts-enabled": True})
    elif settings.s3_sts_mode == "disabled":
        STORAGE_CONFIGS.append({"type": "aws", "sts-enabled": False})
    else:
        raise ValueError(
            f"Invalid LAKEKEEPER_TEST__S3_STS_MODE: {settings.s3_sts_mode}. "
            "must be one of 'both', 'enabled', 'disabled'"
        )

if settings.azure_client_id is not None:
    STORAGE_CONFIGS.append({"type": "azure"})

if settings.gcs_credential is not None:
    STORAGE_CONFIGS.append({"type": "gcs"})


def string_to_bool(s: str) -> bool:
    if s is None:
        return False
    return s.lower() in ["true", "1"]


def filter_empty_str(s: Optional[str]) -> Optional[str]:
    if s is None or s == "":
        return None
    return s


@pytest.fixture(scope="session", params=STORAGE_CONFIGS)
def storage_config(request) -> dict:
    path_style_access = string_to_bool(settings.s3_path_style_access)

    test_id = uuid.uuid4().hex

    if request.param["type"] == "s3":
        if settings.s3_bucket is None or settings.s3_bucket == "":
            pytest.skip("LAKEKEEPER_TEST__S3_BUCKET is not set")

        if settings.s3_region is None:
            pytest.skip("LAKEKEEPER_TEST__S3_REGION is not set")

        extra_config = {}
        if settings.s3_allow_alternative_protocols:
            extra_config["allow-alternative-protocols"] = True

        return {
            "storage-profile": {
                "type": "s3",
                "bucket": settings.s3_bucket,
                "region": settings.s3_region,
                "path-style-access": path_style_access,
                "endpoint": settings.s3_endpoint,
                "key-prefix": test_id,
                "flavor": "minio",
                "sts-enabled": request.param["sts-enabled"],
                **extra_config,
            },
            "storage-credential": {
                "type": "s3",
                "credential-type": "access-key",
                "aws-access-key-id": settings.s3_access_key,
                "aws-secret-access-key": settings.s3_secret_key,
            },
        }
    elif request.param["type"] == "aws":
        if settings.aws_s3_bucket is None or settings.aws_s3_bucket == "":
            pytest.skip("LAKEKEEPER_TEST__AWS_S3_BUCKET is not set")

        aws_s3_key_prefix = filter_empty_str(settings.aws_s3_key_prefix) or ""
        aws_s3_key_prefix = aws_s3_key_prefix.rstrip("/") + "/" + test_id
        assume_role_arn = filter_empty_str(settings.aws_s3_assume_role_arn)
        external_id = filter_empty_str(settings.aws_s3_assume_role_external_id)
        aws_s3_sts_role_arn = filter_empty_str(settings.aws_s3_sts_role_arn)

        if settings.aws_s3_region is None:
            pytest.skip("LAKEKEEPER_TEST__AWS_S3_REGION is not set")

        storage_profile = {
            "type": "s3",
            "bucket": settings.aws_s3_bucket,
            "region": settings.aws_s3_region,
            "path-style-access": path_style_access,
            "key-prefix": aws_s3_key_prefix,
            "flavor": "aws",
            "assume-role-arn": assume_role_arn,
            "sts-enabled": request.param["sts-enabled"],
            "sts-role-arn": (
                aws_s3_sts_role_arn if request.param["sts-enabled"] else None
            ),
        }

        if settings.aws_s3_use_system_identity:
            storage_credential = {
                "type": "s3",
                "credential-type": "aws-system-identity",
                "external-id": external_id,
            }

        else:
            storage_credential = {
                "type": "s3",
                "credential-type": "access-key",
                "aws-access-key-id": settings.aws_s3_access_key,
                "aws-secret-access-key": settings.aws_s3_secret_access_key,
            }

        return {
            "storage-profile": storage_profile,
            "storage-credential": storage_credential,
        }
    elif request.param["type"] == "azure":
        if (
            settings.azure_storage_account_name is None
            or settings.azure_storage_account_name == ""
        ):
            pytest.skip("LAKEKEEPER_TEST__AZURE_STORAGE_ACCOUNT_NAME is not set")
        if (
            settings.azure_storage_filesystem is None
            or settings.azure_storage_filesystem == ""
        ):
            pytest.skip("LAKEKEEPER_TEST__AZURE_STORAGE_FILESYSTEM is not set")

        extra_config = {}
        if settings.azure_allow_alternative_protocols:
            extra_config["allow-alternative-protocols"] = True

        return {
            "storage-profile": {
                "type": "adls",
                "account-name": settings.azure_storage_account_name,
                "filesystem": settings.azure_storage_filesystem,
                **extra_config,
                "key-prefix": test_id,
            },
            "storage-credential": {
                "type": "az",
                "credential-type": "client-credentials",
                "client-id": settings.azure_client_id,
                "client-secret": settings.azure_client_secret,
                "tenant-id": settings.azure_tenant_id,
            },
        }
    elif request.param["type"] == "gcs":
        if settings.gcs_bucket is None or settings.gcs_bucket == "":
            pytest.skip("LAKEKEEPER_TEST__GCS_BUCKET is not set")

        return {
            "storage-profile": {
                "type": "gcs",
                "bucket": settings.gcs_bucket,
                "key-prefix": test_id,
            },
            "storage-credential": {
                "type": "gcs",
                "credential-type": "service-account-key",
                "key": json.loads(settings.gcs_credential),
            },
        }
    else:
        raise ValueError(f"Unknown storage type: {request.param['type']}")


@pytest.fixture(scope="session")
def io_fsspec(storage_config: dict):
    import fsspec

    if storage_config["storage-profile"]["type"] == "s3":
        client_kwargs = {
            "region_name": storage_config["storage-profile"]["region"],
            "use_ssl": False,
        }
        if "endpoint" in storage_config["storage-profile"]:
            client_kwargs["endpoint_url"] = storage_config["storage-profile"][
                "endpoint"
            ]

        if "aws-access-key-id" not in storage_config["storage-credential"]:
            pytest.skip("`aws-access-key-id` not found in storage-credential. ")

        fs = fsspec.filesystem(
            "s3",
            anon=False,
            key=storage_config["storage-credential"]["aws-access-key-id"],
            secret=storage_config["storage-credential"]["aws-secret-access-key"],
            client_kwargs=client_kwargs,
        )

        return fs
    if storage_config["storage-profile"]["type"] == "adls":
        fs = fsspec.filesystem(
            "abfs",
            account_name=storage_config["storage-profile"]["account-name"],
            tenant_id=storage_config["storage-credential"]["tenant-id"],
            client_id=storage_config["storage-credential"]["client-id"],
            client_secret=storage_config["storage-credential"]["client-secret"],
        )
        return fs


@dataclasses.dataclass
class Server:
    catalog_url: str
    management_url: str
    access_token: str

    def create_project(self, name: str) -> uuid.UUID:
        create_payload = {"project-name": name}
        project_url = self.project_url
        response = requests.post(
            project_url,
            json=create_payload,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        if not response.ok:
            raise ValueError(
                f"Failed to create project ({response.status_code}): {response.text}"
            )

        project_id = response.json()["project-id"]
        return uuid.UUID(project_id)

    def create_warehouse(
        self, name: str, project_id: uuid.UUID, storage_config: dict
    ) -> uuid.UUID:
        """Create a warehouse in this server"""

        create_payload = {
            "project-id": str(project_id),
            "warehouse-name": name,
            **storage_config,
            "delete-profile": {"type": "soft", "expiration-seconds": 2},
        }

        warehouse_url = self.warehouse_url
        response = requests.post(
            warehouse_url,
            json=create_payload,
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        try:
            response.raise_for_status()
        except Exception as e:
            raise ValueError(
                f"Failed to create warehouse ({response.status_code}): {response.text}."
            )

        warehouse_id = response.json()["warehouse-id"]
        print(f"Created warehouse {name} with ID {warehouse_id}")
        return uuid.UUID(warehouse_id)

    @property
    def warehouse_url(self) -> str:
        return urllib.parse.urljoin(self.management_url, "v1/warehouse")

    @property
    def project_url(self) -> str:
        return urllib.parse.urljoin(self.management_url, "v1/project")

    @property
    def user_url(self) -> str:
        return urllib.parse.urljoin(self.management_url, "v1/user")

    @property
    def openfga_permissions_url(self) -> str:
        server_info = requests.get(
            self.management_url + "v1/info",
            headers={"Authorization": f"Bearer {self.access_token}"},
        )
        server_info.raise_for_status()
        server_info = server_info.json()

        if server_info.get("authz-backend") != "openfga":
            pytest.skip("Server is not using OpenFGA as authz backend. Skipping test.")

        return urllib.parse.urljoin(self.management_url, "v1/permissions")


@dataclasses.dataclass
class Warehouse:
    server: Server
    project_id: uuid.UUID
    warehouse_id: uuid.UUID
    warehouse_name: str
    access_token: str

    @property
    def pyiceberg_catalog(self) -> pyiceberg.catalog.rest.RestCatalog:
        return pyiceberg.catalog.rest.RestCatalog(
            name="my_catalog_name",
            uri=self.server.catalog_url,
            warehouse=f"{self.project_id}/{self.warehouse_name}",
            token=self.access_token,
        )

    @property
    def normalized_catalog_name(self) -> str:
        return f"catalog_{self.warehouse_name.replace('-', '_')}"


@dataclasses.dataclass
class Namespace:
    name: pyiceberg.typedef.Identifier
    warehouse: Warehouse

    @property
    def pyiceberg_catalog(self) -> pyiceberg.catalog.rest.RestCatalog:
        return self.warehouse.pyiceberg_catalog

    @property
    def spark_name(self) -> str:
        return "`" + ".".join(self.name) + "`"

    @property
    def url_name(self) -> str:
        return "%1F".join(self.name)


@pytest.fixture(scope="session")
def access_token() -> str:
    if settings.openid_provider_uri is None:
        pytest.skip("OAUTH_PROVIDER_URI is not set")

    token_endpoint = requests.get(
        str(settings.openid_provider_uri).strip("/")
        + "/.well-known/openid-configuration"
    ).json()["token_endpoint"]
    response = requests.post(
        token_endpoint,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.openid_client_id,
            "client_secret": settings.openid_client_secret,
            "scope": settings.openid_scope,
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


@pytest.fixture(scope="session")
def trino_token() -> str:
    if settings.openid_provider_uri is None:
        pytest.skip("OAUTH_PROVIDER_URI is not set")

    token_endpoint = requests.get(
        str(settings.openid_provider_uri).strip("/")
        + "/.well-known/openid-configuration"
    ).json()["token_endpoint"]
    response = requests.post(
        token_endpoint,
        data={
            "grant_type": "client_credentials",
            "client_id": settings.openid_client_id,
            "client_secret": settings.openid_client_secret,
            "scope": "trino",
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


@pytest.fixture(scope="session")
def server(access_token) -> Server:
    if settings.management_url is None:
        pytest.skip("LAKEKEEPER_TEST__MANAGEMENT_URL is not set")
    if settings.catalog_url is None:
        pytest.skip("LAKEKEEPER_TEST__CATALOG_URL is not set")

    # Bootstrap the server if is not yet boostrapped
    management_url = settings.management_url.rstrip("/") + "/"
    server_info = requests.get(
        management_url + "v1/info", headers={"Authorization": f"Bearer {access_token}"}
    )
    server_info.raise_for_status()
    server_info = server_info.json()
    if not server_info["bootstrapped"]:
        response = requests.post(
            management_url + "v1/bootstrap",
            headers={"Authorization": f"Bearer {access_token}"},
            json={"accept-terms-of-use": True},
        )
        response.raise_for_status()

    return Server(
        catalog_url=settings.catalog_url.rstrip("/") + "/",
        management_url=settings.management_url.rstrip("/") + "/",
        access_token=access_token,
    )


@pytest.fixture(scope="session")
def project(server: Server) -> uuid.UUID:
    if settings.use_default_project is None:
        test_id = uuid.uuid4()
        project_name = f"project-{test_id}"
        project_id = server.create_project(project_name)
    else:
        project_id = uuid.UUID("{00000000-0000-0000-0000-000000000000}")
    return project_id


@pytest.fixture(scope="session")
def warehouse(server: Server, storage_config, project) -> Warehouse:
    if settings.lakekeeper_warehouse is None:
        test_id = uuid.uuid4()
        warehouse_name = f"warehouse-{test_id}"
    else:
        warehouse_name = settings.lakekeeper_warehouse

    warehouse_id = server.create_warehouse(
        warehouse_name, project_id=project, storage_config=storage_config
    )

    return Warehouse(
        access_token=server.access_token,
        server=server,
        project_id=project,
        warehouse_id=warehouse_id,
        warehouse_name=warehouse_name,
    )


@pytest.fixture(scope="function")
def namespace(warehouse: Warehouse) -> Namespace:
    catalog = warehouse.pyiceberg_catalog
    namespace = (f"namespace-{uuid.uuid4()}",)
    catalog.create_namespace(namespace)
    return Namespace(name=namespace, warehouse=warehouse)


@pytest.fixture(scope="session")
def spark(warehouse: Warehouse, storage_config):
    """Spark with a pre-configured Iceberg catalog"""
    try:
        import findspark

        findspark.init()
    except ImportError:
        pytest.skip("findspark not installed")

    import pyspark
    import pyspark.sql

    pyspark_version = pyspark.__version__
    # Strip patch version
    pyspark_version = ".".join(pyspark_version.split(".")[:2])

    print(f"SPARK_ICEBERG_VERSION: {settings.spark_iceberg_version}")
    spark_jars_packages = (
        f"org.apache.iceberg:iceberg-spark-runtime-{pyspark_version}_2.12:{settings.spark_iceberg_version},"
        f"org.apache.iceberg:iceberg-aws-bundle:{settings.spark_iceberg_version},"
        f"org.apache.iceberg:iceberg-azure-bundle:{settings.spark_iceberg_version},"
        f"org.apache.iceberg:iceberg-gcp-bundle:{settings.spark_iceberg_version}"
    )

    # random 5 char string
    catalog_name = warehouse.normalized_catalog_name
    configuration = {
        "spark.jars.packages": spark_jars_packages,
        "spark.sql.extensions": "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        "spark.sql.defaultCatalog": catalog_name,
        f"spark.sql.catalog.{catalog_name}": "org.apache.iceberg.spark.SparkCatalog",
        f"spark.sql.catalog.{catalog_name}.catalog-impl": "org.apache.iceberg.rest.RESTCatalog",
        f"spark.sql.catalog.{catalog_name}.uri": warehouse.server.catalog_url,
        f"spark.sql.catalog.{catalog_name}.credential": f"{settings.openid_client_id}:{settings.openid_client_secret}",
        f"spark.sql.catalog.{catalog_name}.warehouse": f"{warehouse.project_id}/{warehouse.warehouse_name}",
        f"spark.sql.catalog.{catalog_name}.scope": settings.openid_scope,
        f"spark.sql.catalog.{catalog_name}.oauth2-server-uri": f"{settings.token_endpoint}",
        f"spark.sql.catalog.{catalog_name}.cache-enabled": "false",
    }
    if (
        storage_config["storage-profile"]["type"] == "s3"
        and storage_config["storage-profile"]["sts-enabled"]
    ):
        configuration[
            f"spark.sql.catalog.{catalog_name}.header.X-Iceberg-Access-Delegation"
        ] = "vended-credentials"
    elif storage_config["storage-profile"]["type"] == "s3":
        configuration[
            f"spark.sql.catalog.{catalog_name}.header.X-Iceberg-Access-Delegation"
        ] = "remote-signing"

    spark_conf = pyspark.SparkConf().setMaster("local[*]")

    for k, v in configuration.items():
        spark_conf = spark_conf.set(k, v)

    spark = pyspark.sql.SparkSession.builder.config(conf=spark_conf).getOrCreate()
    spark.sql(f"USE {catalog_name}")
    yield spark
    spark.stop()


@pytest.fixture(scope="session")
def trino(warehouse: Warehouse, storage_config, trino_token):
    if settings.trino_uri is None:
        pytest.skip("LAKEKEEPER_TEST__TRINO_URI is not set")

    from trino.dbapi import connect
    from trino.auth import JWTAuthentication

    if settings.trino_auth_enabled == "true":
        conn = connect(
            host=settings.trino_uri,
            auth=JWTAuthentication(trino_token),
            http_scheme="https",
            verify=False,  # Self-signed certificate
        )
    else:
        conn = connect(host=settings.trino_uri, user="trino")

    cur = conn.cursor()
    if storage_config["storage-profile"]["type"] == "s3":
        extra_config = f"""
            ,
            "s3.region" = 'dummy',
            "s3.path-style-access" = 'true',
            "s3.endpoint" = '{settings.s3_endpoint}',
            "fs.native-s3.enabled" = 'true'
        """
    elif storage_config["storage-profile"]["type"] == "adls":
        # Azure currently without vended creds :/ https://github.com/trinodb/trino/issues/23238
        extra_config = f"""
            ,
            "fs.native-azure.enabled" = 'true',
            "azure.auth-type" = 'OAUTH',
            "azure.oauth.client-id" = '{settings.azure_client_id}',
            "azure.oauth.secret" = '{settings.azure_client_secret}',
            "azure.oauth.tenant-id" = '{settings.azure_tenant_id}',
            "azure.oauth.endpoint" = 'https://login.microsoftonline.com/{settings.azure_tenant_id}/v2.0'
        """
    else:
        raise ValueError(
            f"Unknown storage type: {storage_config['storage-profile']['type']}"
        )

    cur.execute(
        f"""
        CREATE CATALOG {warehouse.normalized_catalog_name} USING iceberg
        WITH (
            "iceberg.catalog.type" = 'rest',
            "iceberg.rest-catalog.uri" = '{warehouse.server.catalog_url}',
            "iceberg.rest-catalog.warehouse" = '{warehouse.project_id}/{warehouse.warehouse_name}',
            "iceberg.rest-catalog.security" = 'OAUTH2',
            "iceberg.rest-catalog.oauth2.credential" = '{settings.openid_client_id}:{settings.openid_client_secret}',
            "iceberg.rest-catalog.oauth2.scope" = '{settings.openid_scope}',
            "iceberg.rest-catalog.oauth2.server-uri" = '{settings.token_endpoint}',
            "iceberg.rest-catalog.nested-namespace-enabled" = 'true',
            "iceberg.rest-catalog.vended-credentials-enabled" = 'true',
            "iceberg.unique-table-location" = 'true'
            {extra_config}
        )
    """
    )

    # Connect to specific catalog to avoid prefixes
    if settings.trino_auth_enabled == "true":
        conn = connect(
            host=settings.trino_uri,
            auth=JWTAuthentication(trino_token),
            http_scheme="https",
            verify=False,  # Self-signed certificate
            catalog=warehouse.normalized_catalog_name,
        )
    else:
        conn = connect(
            host=settings.trino_uri,
            user="trino",
            catalog=warehouse.normalized_catalog_name,
        )

    yield conn


@pytest.fixture(scope="session")
def starrocks(warehouse: Warehouse, storage_config):
    if settings.starrocks_uri is None:
        pytest.skip("LAKEKEEPER_TEST__STARROCKS_URI is not set")

    from sqlalchemy import create_engine

    engine = create_engine(settings.starrocks_uri)
    connection = engine.connect()
    connection.execute("DROP CATALOG IF EXISTS rest_catalog")

    storage_type = storage_config["storage-profile"]["type"]

    if storage_type == "s3":
        # Use the following when https://github.com/StarRocks/starrocks/issues/50585#issue-2501162084
        # is fixed:
        # connection.execute(
        #     f"""
        #     CREATE EXTERNAL CATALOG rest_catalog
        #     PROPERTIES
        #     (
        #         "type" = "iceberg",
        #         "iceberg.catalog.type" = "rest",
        #         "iceberg.catalog.uri" = "{warehouse.server.catalog_url}",
        #         "iceberg.catalog.warehouse" = "{warehouse.project_id}/{warehouse.warehouse_name}",
        #         "header.x-iceberg-access-delegation" = "vended-credentials",
        #         "iceberg.catalog.oauth2-server-uri" = "{settings.openid_provider_uri.rstrip('/')}/protocol/openid-connect/token",
        #         "iceberg.catalog.credential" = "{settings.openid_client_id}:{settings.openid_client_secret}"
        #     )
        #     """
        # )
        connection.execute(
            f"""
            CREATE EXTERNAL CATALOG rest_catalog
            PROPERTIES
            (
                "type" = "iceberg",
                "iceberg.catalog.type" = "rest",
                "iceberg.catalog.uri" = "{warehouse.server.catalog_url}",
                "iceberg.catalog.warehouse" = "{warehouse.project_id}/{warehouse.warehouse_name}",
                "iceberg.catalog.oauth2-server-uri" = "{settings.token_endpoint}",
                "iceberg.catalog.credential" = "{settings.openid_client_id}:{settings.openid_client_secret}",
                "iceberg.catalog.scope" = "lakekeeper",
                "aws.s3.region" = "local",
                "aws.s3.enable_path_style_access" = "true",
                "aws.s3.endpoint" = "{settings.s3_endpoint}",
                "aws.s3.access_key" = "{settings.s3_access_key}",
                "aws.s3.secret_key" = "{settings.s3_secret_key}"
            )
            """
        )
    else:
        raise ValueError(f"Unknown storage type for starrocks: {storage_type}")
    connection.execute("SET CATALOG rest_catalog")

    yield connection
