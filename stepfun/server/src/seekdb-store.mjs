/**
 * 本地 seekdb 封装：集合结构、写入、索引刷新、混合检索。
 *
 * 关键设计（对应赛题要求）：
 * - 向量由浏览器端算好后传入（embeddingFunction = null），服务端不做任何推理；
 * - 全文检索与向量检索都由 seekdb 完成，混合检索调用 collection.hybridSearch()，
 *   不在客户端把两路结果各自取回再拼合；
 * - 每次批量写入后调用 refresh_index()，保证增量导入的内容立即可被检索到。
 */
import {
  SeekdbClient,
  Schema,
  VectorIndexConfig,
  FulltextIndexConfig,
  registerEmbeddingFunction,
} from "seekdb";

export const DEFAULT_DIMENSION = 512; // bge-small-zh-v1.5 的向量维度

/**
 * 端侧 embedding 占位函数。
 *
 * seekdb-js 会为集合登记一个 embedding 函数名，服务端在缺少向量时会尝试调用它。
 * 本应用的向量全部由浏览器计算后传入，因此这里注册一个"只声明维度、绝不产出向量"的函数：
 * 一旦服务端试图代算向量就会直接报错，既避免误用，也证明向量确实来自端侧。
 */
class ClientSideEmbeddingFunction {
  name = "default-embed";
  dimension = DEFAULT_DIMENSION;
  constructor(config = {}) {
    this.dimension = config.dimension ?? DEFAULT_DIMENSION;
  }
  async generate() {
    throw new Error(
      "端侧推理模式：向量必须由浏览器计算后随写入请求传入，服务端不做任何 embedding 计算。",
    );
  }
  getConfig() {
    return { dimension: this.dimension };
  }
  static buildFromConfig(config = {}) {
    return new ClientSideEmbeddingFunction(config);
  }
}

registerEmbeddingFunction("default-embed", ClientSideEmbeddingFunction);

/** 建立集合 schema：全文(ik 中文分词) + 稠密向量(HNSW, cosine)。 */
export function buildSchema(dimension = DEFAULT_DIMENSION) {
  return new Schema({
    fulltextIndex: new FulltextIndexConfig("ik", { ik_mode: "smart" }),
    vectorIndex: new VectorIndexConfig({
      hnsw: { dimension, distance: "cosine" },
      // 显式指向端侧占位函数：向量由浏览器算好后传入，服务端不参与推理
      embeddingFunction: new ClientSideEmbeddingFunction({ dimension }),
    }),
  });
}

/** 连接一个已启动的 seekdb 实例（server 模式）。 */
export function createClient(endpoint) {
  return new SeekdbClient({
    host: endpoint.host,
    port: endpoint.port,
    user: endpoint.user,
    password: "",
    database: "test",
  });
}

/**
 * 真正清空集合：SDK 的 delete() 是软删除（表还在、行还在），
 * 这里直接 DROP 集合内部表并清掉 SDK 的集合元数据，再重新建集合，
 * 保证"切换精度/重新导入后，检索结果只反映当前知识库"。
 */
export async function resetCollection(client, { name, dimension }) {
  const tables = await client.execute("SHOW TABLES");
  const tableNames = tables.map((row) => Object.values(row)[0]).filter(Boolean);
  for (const table of tableNames) {
    if (table === "sdk_collections") continue;
    try {
      await client.execute(`DROP TABLE \`${table}\``);
    } catch (error) {
      /* 忽略无法删除的表 */
    }
  }
  try {
    await client.execute("DELETE FROM sdk_collections WHERE collection_name = ?", [name]);
  } catch {
    /* 元数据清理失败不阻断重建 */
  }
  const collection = await client.createCollection({ name, schema: buildSchema(dimension) });
  return { client, collection };
}
/**
 * 维度一致性：换了模型（维度不同）时重建集合，保证索引与向量维度匹配。
 */
export async function ensureDimension(client, { name, dimension }) {
  try {
    const collection = await client.getCollection({ name });
    if (collection.dimension === dimension) return { client, collection };
  } catch {
    /* 集合不存在，走重建 */
  }
  return resetCollection(client, { name, dimension });
}

/**
 * 取得（必要时创建）集合。
 * @returns {{client: import('seekdb').SeekdbClient, collection: import('seekdb').Collection}}
 */
export async function ensureCollection(client, { name, dimension }) {
  try {
    const collection = await client.getCollection({ name });
    return { client, collection };
  } catch {
    /* 不存在，走创建 */
  }
  try {
    const collection = await client.createCollection({
      name,
      schema: buildSchema(dimension),
    });
    return { client, collection };
  } catch (error) {
    // 幂等：上一次运行已经建好集合（SDK 的 getCollection 可能因元数据不一致而报错）
    const collection = await client.getCollection({ name });
    return { client, collection };
  }
}

/**
 * 写入文档（向量由端侧传入），随后刷新索引。
 * 用 upsert 语义：重复导入同一个文档 id 时覆盖而不是报错，保证连续导入稳定。
 * @param {{ids: string[], documents: string[], metadatas: object[], embeddings: number[][]}} payload
 */
export async function addDocuments(collection, payload) {
  const { ids, documents, metadatas, embeddings } = payload;
  if (!Array.isArray(ids) || ids.length === 0) {
    throw new Error("没有待写入的文档");
  }
  if (!Array.isArray(embeddings) || embeddings.length !== ids.length) {
    throw new Error("向量数量与文档数量不一致");
  }
  const record = { ids, documents, metadatas, embeddings };
  if (typeof collection.upsert === "function") {
    await collection.upsert(record);
  } else {
    await collection.add(record);
  }
  await collection.refresh_index();
}

/**
 * 混合检索：全文关键词 + 向量语义，由 seekdb 完成排序。
 * @param {{collection: import('seekdb').Collection, keyword?: string, queryEmbedding: number[], nResults?: number}} args
 */
export async function hybridSearch({ collection, keyword, queryEmbedding, nResults = 5 }) {
  const knn = { queryEmbeddings: [queryEmbedding] };
  const options = { knn, nResults };
  if (keyword && keyword.trim()) {
    options.query = { whereDocument: { $contains: keyword.trim() } };
  }
  const result = await collection.hybridSearch(options);
  const ids = (result.ids ?? []).flat().filter((v) => v !== null && v !== undefined);
  const documents = (result.documents ?? []).flat().filter((v) => v !== null && v !== undefined);
  const metadatas = (result.metadatas ?? []).flat().filter((v) => v !== null && v !== undefined);
  const distances = (result.distances ?? []).flat().filter((v) => v !== null && v !== undefined);
  return {
    ids,
    documents,
    metadatas,
    distances,
    items: ids.map((id, index) => ({
      id,
      document: documents[index] ?? "",
      metadata: metadatas[index] ?? {},
      distance: distances[index] ?? null,
    })),
  };
}
