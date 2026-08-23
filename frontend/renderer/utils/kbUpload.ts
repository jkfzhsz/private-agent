// P1-3(2026-08-17): 知识库文件上传 —— multipart 主路径 + base64 JSON 兼容回退
// 审计 I4: base64 编码在主线程执行会冻结大文件上传; multipart 由浏览器
// 原生编码(不占 JS 主线程)。本模块为纯函数, doFetch 注入以便单测
// (组件传 adminFetch 保 token; 测试传 mock fetch)。
//
// 回退策略: multipart 优先(新后端), 任一步失败(旧后端 404/422/网络)
// → 回退 base64 JSON(旧接口, 保留一个版本周期)。

export interface KbUploadResult {
  doc_id: number;
  chunks: number;
  filename: string;
}

export type UploadDoFetch = (
  url: string,
  init: RequestInit
) => Promise<Response>;

const UPLOAD_URL = "http://127.0.0.1:8765/admin/knowledge/upload-file";

/** multipart/form-data 主路径(浏览器原生编码, 不占 JS 主线程) */
export async function uploadKbFileMultipart(
  file: File,
  scenario: string,
  doFetch: UploadDoFetch
): Promise<KbUploadResult> {
  const form = new FormData();
  form.append("file", file);
  if (scenario.trim()) form.append("scenario", scenario.trim());
  const resp = await doFetch(UPLOAD_URL, {
    method: "POST",
    // 注意: 不手动设 Content-Type —— 浏览器自动带 multipart boundary
    body: form,
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
  return data as KbUploadResult;
}

/** base64 JSON 兼容回退路径(旧后端) */
export async function uploadKbFileBase64(
  file: File,
  scenario: string,
  doFetch: UploadDoFetch
): Promise<KbUploadResult> {
  const buf = await file.arrayBuffer();
  let binary = "";
  const bytes = new Uint8Array(buf);
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  const body: Record<string, unknown> = {
    filename: file.name,
    content_base64: btoa(binary),
  };
  if (scenario.trim()) body.scenario = scenario.trim();
  const resp = await doFetch(UPLOAD_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error ?? `HTTP ${resp.status}`);
  return data as KbUploadResult;
}

/** 主入口: multipart 优先, 失败回退 base64 */
export async function uploadKbFile(
  file: File,
  scenario: string,
  doFetch: UploadDoFetch
): Promise<KbUploadResult> {
  try {
    return await uploadKbFileMultipart(file, scenario, doFetch);
  } catch (e) {
    // 旧后端(未部署 multipart 契约)或网络层失败 → 回退
    console.warn("[kb-upload] multipart 失败, 回退 base64:", e);
    return await uploadKbFileBase64(file, scenario, doFetch);
  }
}
