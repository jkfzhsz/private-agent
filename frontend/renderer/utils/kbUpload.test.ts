// P1-3(2026-08-17): 知识库上传双路径 —— multipart 主路径 + base64 回退
import { describe, expect, it, vi } from "vitest";
import {
  uploadKbFile,
  uploadKbFileBase64,
  uploadKbFileMultipart,
} from "./kbUpload";

function okResp(body: unknown): Response {
  return {
    ok: true,
    status: 200,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

function errResp(status: number, error: string): Response {
  return {
    ok: false,
    status,
    json: () => Promise.resolve({ error }),
  } as unknown as Response;
}

function makeFile(name: string, content: string): File {
  const f = new File([content], name, { type: "text/plain" }) as File & {
    arrayBuffer?: () => Promise<ArrayBuffer>;
  };
  // Node 环境的 File 缺 arrayBuffer(), 补一个(jsdom 同样)
  f.arrayBuffer = () =>
    Promise.resolve(new TextEncoder().encode(content).buffer as ArrayBuffer);
  return f;
}

describe("P1-3 kbUpload 双路径", () => {
  it("multipart 主路径: body 为 FormData, 不设 Content-Type", async () => {
    const doFetch = vi.fn(async (_url: string, init: RequestInit) => {
      expect(init.body).toBeInstanceOf(FormData);
      expect((init.headers as Headers | undefined)?.get?.("Content-Type")).toBeUndefined();
      return okResp({ doc_id: 7, chunks: 3, filename: "a.md" });
    });
    const r = await uploadKbFileMultipart(
      makeFile("a.md", "内容"),
      "office",
      doFetch as unknown as (url: string, init: RequestInit) => Promise<Response>
    );
    expect(r.doc_id).toBe(7);
    expect(r.chunks).toBe(3);
    expect(doFetch).toHaveBeenCalledTimes(1);
  });

  it("base64 回退路径: body 为 JSON 字符串且含 content_base64", async () => {
    const doFetch = vi.fn(async (_url: string, init: RequestInit) => {
      expect(init.body).toBeTypeOf("string");
      const parsed = JSON.parse(String(init.body));
      expect(parsed.filename).toBe("a.md");
      expect(parsed.content_base64.length).toBeGreaterThan(0);
      return okResp({ doc_id: 9, chunks: 2, filename: "a.md" });
    });
    const r = await uploadKbFileBase64(
      makeFile("a.md", "内容"),
      "",
      doFetch as unknown as (url: string, init: RequestInit) => Promise<Response>
    );
    expect(r.doc_id).toBe(9);
  });

  it("主入口: multipart 失败 → 自动回退 base64", async () => {
    const doFetch = vi
      .fn()
      .mockImplementationOnce(async () => errResp(422, "unprocessable"))
      .mockImplementationOnce(async () => okResp({ doc_id: 11, chunks: 1, filename: "a.md" }));
    const r = await uploadKbFile(
      makeFile("a.md", "内容"),
      "office",
      doFetch as unknown as (url: string, init: RequestInit) => Promise<Response>
    );
    expect(r.doc_id).toBe(11);
    expect(doFetch).toHaveBeenCalledTimes(2);
  });

  it("主入口: multipart 成功 → 不触发回退", async () => {
    const doFetch = vi.fn(async () => okResp({ doc_id: 13, chunks: 4, filename: "a.md" }));
    const r = await uploadKbFile(
      makeFile("a.md", "内容"),
      "office",
      doFetch as unknown as (url: string, init: RequestInit) => Promise<Response>
    );
    expect(r.doc_id).toBe(13);
    expect(doFetch).toHaveBeenCalledTimes(1);
  });

  it("主入口: 双路径均失败 → 抛出最后错误", async () => {
    const doFetch = vi
      .fn()
      .mockImplementationOnce(async () => errResp(500, "upload_failed"))
      .mockImplementationOnce(async () => errResp(500, "upload_failed"));
    await expect(
      uploadKbFile(
        makeFile("a.md", "内容"),
        "office",
        doFetch as unknown as (url: string, init: RequestInit) => Promise<Response>
      )
    ).rejects.toThrow("upload_failed");
    expect(doFetch).toHaveBeenCalledTimes(2);
  });
});
