use async_trait::async_trait;
use axum::{
    extract::{Path, State},
    routing::post,
    Extension, Json, Router,
};
use iceberg_ext::catalog::rest::{S3SignRequest, S3SignResponse};

use super::{ApiContext, Prefix, Result};
use crate::request_metadata::RequestMetadata;

#[async_trait]
pub trait Service<S: crate::api::ThreadSafe>
where
    Self: Send + Sync + 'static,
{
    /// Sign an S3 request.
    /// Requests should be send to `/:prefix/namespace/:namespace/table/:table/v1/aws/s3/sign`,
    /// where :namespace and :table can be any string. Typically these strings would be
    /// ids of the namespace and table, respectively - not their names.
    /// For clients to use this route, the server implementation should specify "s3.signer.uri"
    /// accordingly on `load_table` and other methods that require data access.
    ///
    /// If a request is recieved at `/aws/s3/sign`, table and namespace will be `None`.
    async fn sign(
        prefix: Option<Prefix>,
        tabular_id: Option<uuid::Uuid>,
        request: S3SignRequest,
        state: ApiContext<S>,
        request_metadata: RequestMetadata,
    ) -> Result<S3SignResponse>;
}

pub fn router<I: Service<S>, S: crate::api::ThreadSafe>() -> Router<ApiContext<S>> {
    Router::new()
        .route(
            "/aws/s3/sign",
            post(
                |State(api_context): State<ApiContext<S>>,
                 Extension(metadata): Extension<RequestMetadata>,
                 Json(request): Json<S3SignRequest>| {
                    {
                        I::sign(None, None, request, api_context, metadata)
                    }
                },
            ),
        )
        .route(
            "/{prefix}/v1/aws/s3/sign",
            post(
                |Path(prefix): Path<Prefix>,
                 State(api_context): State<ApiContext<S>>,
                 Extension(metadata): Extension<RequestMetadata>,
                 Json(request): Json<S3SignRequest>| {
                    {
                        I::sign(Some(prefix), None, request, api_context, metadata)
                    }
                },
            ),
        )
        .route(
            "/signer/{prefix}/tabular-id/{tabular_id}/v1/aws/s3/sign",
            post(
                |Path((prefix, tabular_id)): Path<(Prefix, uuid::Uuid)>,
                 State(api_context): State<ApiContext<S>>,
                 Extension(metadata): Extension<RequestMetadata>,
                 Json(request): Json<S3SignRequest>| {
                    {
                        I::sign(
                            Some(prefix),
                            Some(tabular_id),
                            request,
                            api_context,
                            metadata,
                        )
                    }
                },
            ),
        )
}
