declare namespace Cloudflare {
  interface Env {
    DB?: D1Database;
    BUCKET?: R2Bucket;
    OPENAI_API_KEY?: string;
    OPENAI_MODEL?: string;
    GEMINI_KEY?: string;
    INGEST_TOKEN?: string;
    DDF_CLIENT_ID?: string;
    DDF_CLIENT_SECRET?: string;
    GOOGLE_MAPS_KEY?: string;
  }
}
