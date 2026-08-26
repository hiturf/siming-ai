import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Progress,
  Input,
  InputNumber,
  Modal,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  CloudDownloadOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  FolderOpenOutlined,
  PlayCircleOutlined,
  PoweroffOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import { apiClient } from '../../api/client'
import { useGlobalModelActions } from '../../shared/query/modelConfigs'
import type {
  CatalogResponse,
  DownloadTask,
  HardwareProfile,
  LocalModel,
  LocalModelQualification,
} from './types'

const { Text } = Typography

const formatBytes = (value?: number | null) => {
  if (!value) return '未知'
  const units = ['B', 'KB', 'MB', 'GB']
  let current = value
  let index = 0
  while (current >= 1024 && index < units.length - 1) {
    current /= 1024
    index += 1
  }
  return `${current.toFixed(index >= 3 ? 1 : 0)} ${units[index]}`
}

interface Props {
  hardware: HardwareProfile | null
  catalog: CatalogResponse | null
  downloads: DownloadTask[]
  loading: boolean
  onRefresh: () => Promise<void>
}

export default function ModelCatalogPanel({ hardware, catalog, downloads, loading, onRefresh }: Props) {
  const [modelRoot, setModelRoot] = useState('')
  const [customModel, setCustomModel] = useState({
    modelKey: '', displayName: '', sourceUrl: '', filePath: '', contextLength: 16384,
  })
  const [qualifyingModel, setQualifyingModel] = useState<string | null>(null)
  const [qualification, setQualification] = useState<LocalModelQualification | null>(null)
  const { setGlobalModel } = useGlobalModelActions()
  const usageEnabled = catalog?.usage_enabled !== false
  const usageDisabledReason = catalog?.usage_disabled_reason || '本地 AI 模型暂时已停用，请使用 API 或本机 CLI 模型。'

  const contextForModel = (modelKey?: string | null) => {
    const capacity = catalog?.items.find((item) => item.model_key === modelKey)?.context_length
    const recommended = hardware?.recommended_context || 16384
    return capacity ? Math.min(capacity, recommended) : recommended
  }

  useEffect(() => {
    setModelRoot(catalog?.model_root || '')
  }, [catalog?.model_root])

  const saveModelRoot = async () => {
    try {
      await apiClient.put('/local-models/root', { path: modelRoot })
      message.success('模型目录已更新')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const pickModelRoot = async () => {
    try {
      const response = await apiClient.post<{ data: { path?: string | null; cancelled?: boolean } }>('/local-models/root/pick')
      const path = response.data.data.path
      if (path) setModelRoot(path)
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const pickCustomModel = async () => {
    try {
      const response = await apiClient.post<{ data: { path?: string | null; cancelled?: boolean } }>('/local-models/custom/pick')
      const path = response.data.data.path
      if (path) {
        const filename = path.split(/[\\/]/).pop() || ''
        setCustomModel((current) => ({
          ...current,
          filePath: path,
          modelKey: current.modelKey || filename.replace(/\.gguf$/i, '').replace(/[^A-Za-z0-9_.-]+/g, '-'),
          displayName: current.displayName || filename.replace(/\.gguf$/i, ''),
        }))
      }
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const resumeDownload = async (taskId: string) => {
    try {
      await apiClient.post(`/local-models/downloads/${taskId}/resume`)
      message.success('已从保存的进度继续下载')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const install = async (model: LocalModel) => {
    try {
      await apiClient.post('/local-models/install', { model_key: model.model_key })
      message.success('下载任务已创建，支持断点续传')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const start = async (model: LocalModel) => {
    try {
      await apiClient.post('/local-models/runtime/start', {
        model_key: model.model_key,
        context_length: contextForModel(model.model_key),
        task_type: 'assistant',
      })
      message.success('本地模型已加载')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const stop = async () => {
    await apiClient.post('/local-models/runtime/stop')
    await onRefresh()
  }

  const remove = async (model: LocalModel) => {
    await apiClient.delete(`/local-models/${model.model_key}`)
    message.success('模型文件已删除')
    await onRefresh()
  }

  const makeDefault = async (model: LocalModel) => {
    await setGlobalModel('local_llama_cpp', model.model_key)
    message.success('已设为全局默认离线模型')
  }

  const benchmark = async (model: LocalModel) => {
    const hide = message.loading('正在进行中文生成测速...', 0)
    try {
      const response = await apiClient.post<{ data: any }>('/local-models/benchmark', {
        model_key: model.model_key,
        max_tokens: 128,
      })
      const result = response.data.data
      const outputKind = result.reasoning_only ? '（模型仅返回思考内容，本次仍计入测速）' : ''
      message.success(
        result.tokens_per_second
          ? `${result.tokens_estimated ? '约 ' : ''}${result.tokens_per_second} token/s，用时 ${result.elapsed_seconds}s${outputKind}`
          : `测速完成，用时 ${result.elapsed_seconds}s${outputKind}`,
      )
    } catch (error: any) {
      message.error(error.message)
    } finally {
      hide()
    }
  }

  const qualify = async (model: LocalModel) => {
    setQualifyingModel(model.model_key)
    const hide = message.loading('正在执行真实任务验证，可能需要几分钟…', 0)
    try {
      const response = await apiClient.post<{ data: LocalModelQualification }>('/local-models/qualify', {
        model_key: model.model_key,
        context_length: contextForModel(model.model_key),
      })
      setQualification(response.data.data)
    } catch (error: any) {
      message.error(error.message)
    } finally {
      hide()
      setQualifyingModel(null)
    }
  }

  const downloadCustomModel = async () => {
    try {
      await apiClient.post('/local-models/custom/download', {
        model_key: customModel.modelKey,
        display_name: customModel.displayName,
        source_url: customModel.sourceUrl,
        context_length: customModel.contextLength,
      })
      message.success('自有 GGUF 下载已加入任务中心')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const importCustomModel = async () => {
    try {
      await apiClient.post('/local-models/custom/import', {
        model_key: customModel.modelKey,
        display_name: customModel.displayName,
        file_path: customModel.filePath,
        context_length: customModel.contextLength,
      })
      message.success('自有 GGUF 已登记，可立即加载')
      await onRefresh()
    } catch (error: any) {
      message.error(error.message)
    }
  }

  const visibleDownloads = downloads.filter((item) => !['completed', 'cancelled'].includes(item.status))

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {!usageEnabled && (
        <Alert
          type="warning"
          showIcon
          message="本地 AI 暂停使用"
          description={usageDisabledReason}
        />
      )}

      {hardware && (
        <Card size="small" title="硬件与推荐">
          <Descriptions size="small" column={{ xs: 1, sm: 2, lg: 4 }}>
            <Descriptions.Item label="显卡">{hardware.gpu_name || 'CPU 推理'}</Descriptions.Item>
            <Descriptions.Item label="显存">{hardware.vram_gb || 0} GB</Descriptions.Item>
            <Descriptions.Item label="内存">{hardware.ram_gb} GB</Descriptions.Item>
            <Descriptions.Item label="推荐">
              <Tag color="blue">{hardware.recommended_model}</Tag>
              {hardware.recommended_context / 1024}K 上下文
            </Descriptions.Item>
          </Descriptions>
          {!hardware.training_supported && (
            <Alert
              style={{ marginTop: 12 }}
              type="info"
              showIcon
              message="当前设备可以本地推理；LoRA 训练 Beta 需要至少 8GB 显存的 NVIDIA 显卡。"
            />
          )}
        </Card>
      )}

      <Card size="small" title="模型存储目录">
        <Space.Compact style={{ width: '100%' }}>
          <Input value={modelRoot} onChange={(event) => setModelRoot(event.target.value)} />
          <Button icon={<FolderOpenOutlined />} onClick={pickModelRoot}>选择文件夹</Button>
          <Button onClick={saveModelRoot}>保存</Button>
        </Space.Compact>
      </Card>

      <Card
        size="small"
        title="自有 GGUF 模型"
        extra={<Text type="secondary">不受内置目录限制</Text>}
      >
        <Space direction="vertical" size={8} style={{ width: '100%' }}>
          <Text type="secondary">
            可以直接登记已下载的 GGUF，或给出其直链下载地址。模型来源、许可证和上下文能力由你确认；司命不会把它复制或移动。
          </Text>
          <Space wrap style={{ width: '100%' }}>
            <Input
              aria-label="自有模型标识"
              placeholder="模型标识，例如 qwen36-27b-q4"
              value={customModel.modelKey}
              onChange={(event) => setCustomModel((current) => ({ ...current, modelKey: event.target.value }))}
              style={{ width: 230 }}
            />
            <Input
              aria-label="自有模型名称"
              placeholder="显示名称"
              value={customModel.displayName}
              onChange={(event) => setCustomModel((current) => ({ ...current, displayName: event.target.value }))}
              style={{ width: 220 }}
            />
            <InputNumber
              aria-label="自有模型上下文"
              min={1}
              controls
              value={customModel.contextLength}
              onChange={(value) => setCustomModel((current) => ({ ...current, contextLength: Number(value) || 1 }))}
              addonAfter="tokens"
              style={{ width: 200 }}
            />
          </Space>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              aria-label="GGUF 下载地址"
              placeholder="https://…/model.gguf（直接下载）"
              value={customModel.sourceUrl}
              onChange={(event) => setCustomModel((current) => ({ ...current, sourceUrl: event.target.value }))}
            />
            <Button type="primary" onClick={downloadCustomModel}>下载并登记</Button>
          </Space.Compact>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              aria-label="本机 GGUF 路径"
              placeholder="D:\\Models\\model.gguf（直接登记，不复制）"
              value={customModel.filePath}
              onChange={(event) => setCustomModel((current) => ({ ...current, filePath: event.target.value }))}
            />
            <Button icon={<FolderOpenOutlined />} onClick={pickCustomModel}>选择 GGUF</Button>
            <Button onClick={importCustomModel}>登记本机文件</Button>
          </Space.Compact>
        </Space>
      </Card>

      {visibleDownloads.length > 0 && (
        <Card size="small" title="下载进度">
          <Space direction="vertical" style={{ width: '100%' }}>
            {visibleDownloads.map((task) => {
              const percent = task.total_bytes
                ? Math.min(100, Math.round(task.downloaded_bytes / task.total_bytes * 100))
                : 0
              return (
                <div key={task.id}>
                  <Space style={{ marginBottom: 4 }}>
                    <Text strong>{task.target_key}</Text>
                    <Tag>{task.kind === 'runtime' ? '运行时' : '模型'}</Tag>
                    <Text type="secondary">
                      {formatBytes(task.downloaded_bytes)} / {formatBytes(task.total_bytes)}
                    </Text>
                  </Space>
                  <Space.Compact style={{ width: '100%' }}>
                    <Progress percent={percent} status={task.status === 'failed' ? 'exception' : 'active'} />
                    {task.status === 'failed' && (
                      <Button onClick={() => resumeDownload(task.id)}>重试并续传</Button>
                    )}
                  </Space.Compact>
                  {task.error_message && (
                    <Text type={task.status === 'failed' ? 'danger' : 'warning'}>{task.error_message}</Text>
                  )}
                </div>
              )
            })}
          </Space>
        </Card>
      )}

      <Card
        size="small"
        title="模型目录"
        extra={catalog?.runtime.running ? (
          <Button icon={<PoweroffOutlined />} onClick={stop}>停止运行时</Button>
        ) : null}
      >
        <Table
          rowKey="model_key"
          loading={loading}
          pagination={false}
          dataSource={catalog?.items || []}
          columns={[
            {
              title: '模型',
              render: (_, model: LocalModel) => (
                <Space direction="vertical" size={0}>
                  <Text strong>{model.display_name}</Text>
                  <Text type="secondary">{model.model_key}</Text>
                </Space>
              ),
            },
            {
              title: '规格',
              width: 170,
              render: (_, model: LocalModel) => (
                <Space wrap>
                  <Tag>{model.parameter_size}</Tag>
                  <Tag>{model.quantization}</Tag>
                  <Tag>{model.license_name}</Tag>
                </Space>
              ),
            },
            {
              title: '建议硬件',
              width: 130,
              render: (_, model: LocalModel) =>
                model.recommended_vram_gb != null
                  ? `${model.recommended_vram_gb}GB 显存`
                  : '由用户确认',
            },
            {
              title: '状态',
              width: 120,
              render: (_, model: LocalModel) => {
                const running = catalog?.runtime.running && catalog.runtime.model_key === model.model_key
                if (running) return <Tag color="processing">运行中</Tag>
                if (model.status === 'installed') return <Tag color="success">已安装</Tag>
                return <Tag>未安装</Tag>
              },
            },
            {
              title: '操作',
              width: 450,
              render: (_, model: LocalModel) => model.status !== 'installed' ? (
                <Button
                  type={hardware?.recommended_model === model.model_key ? 'primary' : 'default'}
                  icon={<CloudDownloadOutlined />}
                  onClick={() => install(model)}
                >
                  {hardware?.recommended_model === model.model_key ? '安装推荐模型' : '安装'}
                </Button>
              ) : (
                <Space wrap>
                  <Button disabled={!usageEnabled} icon={<PlayCircleOutlined />} onClick={() => start(model)}>加载</Button>
                  <Button disabled={!usageEnabled} icon={<ThunderboltOutlined />} onClick={() => benchmark(model)}>测速</Button>
                  <Button
                    disabled={!usageEnabled}
                    icon={<ExperimentOutlined />}
                    loading={qualifyingModel === model.model_key}
                    onClick={() => qualify(model)}
                  >
                    任务验证
                  </Button>
                  <Button disabled={!usageEnabled} onClick={() => makeDefault(model)}>设为默认</Button>
                  <Tooltip title="删除模型文件，不删除作品数据">
                    <Button danger icon={<DeleteOutlined />} onClick={() => remove(model)} />
                  </Tooltip>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        open={Boolean(qualification)}
        title="本地模型任务验证"
        width={720}
        footer={<Button type="primary" onClick={() => setQualification(null)}>知道了</Button>}
        onCancel={() => setQualification(null)}
      >
        {qualification && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Alert
              showIcon
              type={qualification.passed ? 'success' : qualification.rating === 'limited' ? 'warning' : 'error'}
              message={qualification.passed ? '当前上下文可完成关键任务' : '当前模型或上下文仅部分合格'}
              description={
                `通过 ${qualification.passed_count}/${qualification.total_count} 项；` +
                `使用 ${Math.round(qualification.context_length / 1024)}K 上下文，耗时 ${qualification.elapsed_seconds} 秒。`
              }
            />
            {qualification.cases.map((item) => (
              <Card
                key={item.id}
                size="small"
                title={item.label}
                extra={<Tag color={item.passed ? 'success' : 'error'}>{item.passed ? '通过' : '未通过'}</Tag>}
              >
                <Space direction="vertical" size={4} style={{ width: '100%' }}>
                  <Text>{item.detail}</Text>
                  <Text type="secondary">
                    输入 {item.input_characters.toLocaleString()} 字符 · {item.elapsed_seconds} 秒
                  </Text>
                  {item.output_preview && (
                    <Typography.Paragraph
                      code
                      ellipsis={{ rows: 3, expandable: true, symbol: '展开模型输出' }}
                      style={{ marginBottom: 0 }}
                    >
                      {item.output_preview}
                    </Typography.Paragraph>
                  )}
                </Space>
              </Card>
            ))}
          </Space>
        )}
      </Modal>
    </Space>
  )
}
