import json
from aws_cdk import (
    Stack,
    Duration,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as cloudfront_origins,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_elasticloadbalancingv2 as elbv2,
    aws_logs as logs,
    aws_route53 as r53,
    aws_route53_targets as r53_targets,
    aws_s3 as s3,
    aws_s3_deployment as s3_deployment,
    aws_secretsmanager as secretsmanager,
    aws_ecr as ecr,
    aws_iam as iam
)
from constructs import Construct

from cdk.util import settings, Props


class ComputeStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, props: Props, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # COMPLETED FOR YOU: ECS cluster for container hosting
        cluster = ecs.Cluster(
            self, f"{settings.PROJECT_NAME}-cluster", vpc=props.network_vpc
        )

        # COMPLETED FOR YOU: Secret for JWT signing key
        app_secret_key = secretsmanager.Secret(
            self,
            f"{settings.PROJECT_NAME}-app-secret-key",
            description="Yoctogram App JWT Signing Key",
        )

        # DATADOG API KEY
        datadog_api_key = secretsmanager.Secret(
            self,
            f"{settings.PROJECT_NAME}-datadog-api-key",
            description="Datadog API Key",
        )

        # FILLMEIN: Fargate task definition
        fargate_task_definition = ecs.FargateTaskDefinition(
            self,
            f"{settings.PROJECT_NAME}-fargate-task-definition",
            cpu=512,
            memory_limit_mib=2048,
            runtime_platform=ecs.RuntimePlatform(
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
                cpu_architecture = ecs.CpuArchitecture.ARM64
            ),

        )

        # FILLMEIN: Grant the task definition's task role access to the database and signing key secrets, as well as the S3 buckets
        app_secret_key.grant_write(fargate_task_definition.task_role)
        props.data_s3_public_images.grant_read_write(fargate_task_definition.task_role)
        props.data_s3_private_images.grant_read_write(fargate_task_definition.task_role)

        # Granting task definition access to Datadog API key
        datadog_api_key.grant_write(fargate_task_definition.task_role)

        # FILLMEIN: Add a container to the Fargate task definition
        secrets = {}
        for secret_key, secret_placeholder in settings.DB_SECRET_MAPPING.items():
            secrets[secret_key] = ecs.Secret.from_secrets_manager(
                secret=props.data_aurora_db.secret,
                field=secret_placeholder
            )
        secrets["SECRET_KEY"] = ecs.Secret.from_secrets_manager(app_secret_key)

        repository = ecr.Repository.from_repository_name(
            self,
            "Repository",
            repository_name="cs40:latest"
        )

        fargate_task_definition.add_container(
            f"{settings.PROJECT_NAME}-app-container",
            container_name=f"{settings.PROJECT_NAME}-app-container",
            logging=ecs.AwsLogDriver(
                stream_prefix=f"{settings.PROJECT_NAME}-fargate",
                log_retention=logs.RetentionDays.ONE_WEEK,
            ),
            #image=ecs.ContainerImage.from_docker_image_asset(
            #    ecr_assets.DockerImageAsset(
            #        self,
            #        "YoctogramBackend",
            #        directory=settings.YOCTOGRAM_APP_DIR
            #    )    
            #),
            image=ecs.ContainerImage.from_ecr_repository(
                repository,
                "latest"
            ),
            port_mappings=[ecs.PortMapping(container_port=80)],
            environment={
                "PRODUCTION": "true",
                "DEBUG": "false",
                "FORWARD_FACING_NAME": f"{settings.PROJECT_NAME}.{settings.SUNET}.{settings.COURSE_DNS_ROOT}",
                "PUBLIC_IMAGES_BUCKET": f"{props.data_s3_public_images.bucket_name}",
                "PRIVATE_IMAGES_BUCKET": f"{props.data_s3_private_images.bucket_name}",
                "PUBLIC_IMAGES_CLOUDFRONT_DISTRIBUTION": f"{props.data_cloudfront_public_images.domain_name}",
                "PRIVATE_IMAGES_CLOUDFRONT_DISTRIBUTION": f"{props.data_cloudfront_private_images.domain_name}",
            },
            secrets=secrets,
            docker_labels={
                "com.datadoghq.ad.instances": '[{"host": "%%host%%", "port": 80}]',
                "com.datadoghq.ad.check_names": '["yoctogram-ecs"]',
                "com.datadoghq.ad.init_configs": "[{}]",
            }
        )

        # DATADOG SIDECAR CONTAINER
        datadog_secrets = {}
        datadog_secrets["DD_API_KEY"] = ecs.Secret.from_secrets_manager(datadog_api_key)

        fargate_task_definition.add_container(
            f"{settings.PROJECT_NAME}-datadog-sidecar-container",
            image=ecs.ContainerImage.from_registry("public.ecr.aws/datadog/agent:latest"),
            environment={
                "ECS_FARGATE": "true",
                "ECS_FARGATE_METRICS": "true",
                "DD_SITE": "us5.datadoghq.com",
                "DD_APM_ENABLED": "true",
                "DD_PROFILING_ENABLED": "true",
                "DD_APM_NON_LOCAL_TRAFFIC": "true"
            },
            secrets=datadog_secrets,
            logging=ecs.AwsLogDriver(
                stream_prefix=f"{settings.PROJECT_NAME}-fargate",
                log_retention=logs.RetentionDays.ONE_WEEK,
            ),
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "agent health"],
                retries=3,
                timeout=Duration.seconds(5),
                interval=Duration.seconds(30),
                start_period=Duration.seconds(15)
            ),
            port_mappings=[
                ecs.PortMapping(
                    container_port=8126,
                    host_port=8126
                )
            ],

        )

        # FILLMEIN: Finish the Fargate service backend deployment
        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            f"{settings.PROJECT_NAME}-fargate-service",
            domain_name=f"api.{settings.PROJECT_NAME}.{settings.SUNET}.{settings.COURSE_DNS_ROOT}",
            certificate=props.network_backend_certificate,
            redirect_http=True,
            cluster=cluster,
            domain_zone=props.network_hosted_zone,
            task_definition=fargate_task_definition
        )

        # COMPLETED FOR YOU: Fargate service settings
        fargate_service.target_group.configure_health_check(path="/api/v1/health")

        fargate_service.service.connections.allow_to(
            props.data_aurora_db, ec2.Port.tcp(5432), "DB access"
        )

        # COMPLETED FOR YOU: S3 frontend deployment setup steps
        frontend_bucket = s3.Bucket(
            self,
            f"{settings.PROJECT_NAME}-frontend-deployment-bucket",
        )

        access_identity = cloudfront.OriginAccessIdentity(
            self,
            f"{settings.PROJECT_NAME}-frontend-access-identity",
        )
        frontend_bucket.grant_read(access_identity)

        frontend_deployment = s3_deployment.BucketDeployment(
            self,
            f"{settings.PROJECT_NAME}-frontend-deployment",
            sources=[s3_deployment.Source.asset(f"{settings.YOCTOGRAM_WEB_DIR}/dist")],
            destination_bucket=frontend_bucket,
        )

        # FILLMEIN: Cloudfront distribution for frontend
        frontend_distribution = cloudfront.Distribution(
            self,
            f"{settings.PROJECT_NAME}-frontend-distribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=cloudfront_origins.S3Origin(
                    bucket=frontend_bucket,
                    origin_access_identity=access_identity,
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS
            ),
            default_root_object="index.html",
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    origin=cloudfront_origins.HttpOrigin(
                        domain_name=f"api.{settings.PROJECT_NAME}.{settings.SUNET}.{settings.COURSE_DNS_ROOT}"
                    ),
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED
                )
            },
            domain_names=[f"{settings.PROJECT_NAME}.{settings.SUNET}.{settings.COURSE_DNS_ROOT}"],
            certificate=props.network_frontend_certificate,
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_http_status=200,
                    response_page_path="/index.html"
                )
            ]
        )


        # COMPLETED FOR YOU: DNS A record for Cloudfront frontend
        frontend_domain = r53.ARecord(
            self,
            f"{settings.PROJECT_NAME}-frontend-domain",
            zone=props.network_hosted_zone,
            record_name=settings.APP_DOMAIN,
            target=r53.RecordTarget.from_alias(
                r53_targets.CloudFrontTarget(frontend_distribution)
            ),
        )

        gh_actions_provider = iam.OpenIdConnectProvider(
            self,
            f"{settings.PROJECT_NAME}-gh-actions-provider",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
        )

        role = iam.Role(
            self,
            "Role",
            assumed_by=iam.WebIdentityPrincipal(
                gh_actions_provider.open_id_connect_provider_arn,
                conditions={
                    "StringLike": {
                         "token.actions.githubusercontent.com:sub": "repo:acwu02/yoctogram-app:ref:refs/heads/main",
                        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                    }
                }
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryFullAccess"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonECS_FullAccess")
            ],
            max_session_duration=Duration.hours(1)
        )
