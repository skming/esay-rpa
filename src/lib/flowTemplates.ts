import { MarkerType, type Edge, type Node } from '@xyflow/react';

import type { RpaNodeData, RuntimeVariable } from '../types/rpa';

export type FlowTemplateId =
  | 'static-list'
  | 'popup-safe-list'
  | 'login-browser'
  | 'search-results'
  | 'api-json'
  | 'pagination-list'
  | 'next-button-pagination'
  | 'infinite-scroll'
  | 'click-load-more'
  | 'list-detail-browser'
  | 'csv-loop-detail';

export type FlowTemplateCategory = 'basic' | 'pagination' | 'interaction' | 'loop';

export const TEMPLATE_CATEGORY_META: Record<FlowTemplateCategory, { label: string; color: string }> = {
  basic: { label: '基础采集', color: '#2563eb' },
  pagination: { label: '翻页采集', color: '#0891b2' },
  interaction: { label: '交互采集', color: '#3733e6' },
  loop: { label: '循环处理', color: '#3733e6' }
};

export type FlowTemplate = {
  category: FlowTemplateCategory;
  description: string;
  id: FlowTemplateId;
  name: string;
  nodes: Node<RpaNodeData>[];
  edges: Edge[];
  variables: RuntimeVariable[];
};

const edgeStyle = { stroke: '#94a3b8', strokeWidth: 1.5 };
const markerEnd = { type: MarkerType.ArrowClosed, color: '#94a3b8' };

export const flowTemplates: FlowTemplate[] = [
  {
    category: 'basic',
    id: 'static-list',
    name: '静态列表采集',
    description: '适合新闻、商品、文章列表页，一步提取文本并写入变量。',
    variables: [],
    nodes: [
      startNode(),
      stepNode('fetch', 500, 110, {
        title: '采集列表文本',
        description: 'quotes.toscrape.com · .quote .text',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.fetch',
          targetUrl: 'https://quotes.toscrape.com/',
          selector: '.quote .text::text',
          fetcher: 'static',
          extractMode: 'text',
          outputVariable: 'list_items',
          firstValueVariable: 'first_item',
          countVariable: 'item_count',
          adaptive: true,
          autoSave: true,
          timeoutMs: 30_000
        }
      }),
      stepNode('write', 500, 230, {
        title: '写入采集结果',
        description: '${var.output_prefix}.txt',
        kind: 'file',
        status: 'pending',
        action: {
          type: 'file.write',
          path: '${var.output_prefix}.txt',
          content: 'first=${var.first_item}; count=${var.item_count}',
          outputVariable: 'report_path',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 350)
    ],
    edges: chainEdges(['start', 'fetch', 'write', 'end'])
  },
  {
    category: 'interaction',
    id: 'popup-safe-list',
    name: '弹窗处理后采集',
    description: '适合存在 Cookie 横幅、订阅弹窗、促销遮罩的列表页面。',
    variables: [variable('popup_url', 'https://example.com/news')],
    nodes: [
      startNode(),
      stepNode('open-popup-page', 500, 90, {
        title: '打开目标页',
        description: '${var.popup_url}',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.open', targetUrl: '${var.popup_url}', timeoutMs: 30_000 }
      }),
      stepNode('dismiss-popups', 500, 210, {
        title: '关闭遮罩弹窗',
        description: 'Cookie / 订阅 / 促销弹窗',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.dismiss',
          selector: 'button.accept-cookie\nbutton[aria-label="Close"]\n.modal .close\n.popup-close',
          targetSelector: '.article-card',
          delayMs: 300,
          maxIterations: 4,
          dismissedCountVariable: 'dismissed_popup_count',
          outputVariable: 'dismiss_result',
          timeoutMs: 30_000
        }
      }),
      stepNode('extract-popup-safe', 500, 330, {
        title: '提取列表内容',
        description: '.article-card',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.extract',
          selector: '.article-card::text',
          outputVariable: 'popup_safe_items',
          firstValueVariable: 'first_popup_safe_item',
          countVariable: 'popup_safe_item_count',
          timeoutMs: 30_000
        }
      }),
      stepNode('write-popup-safe', 500, 450, {
        title: '写入采集摘要',
        description: '${var.output_prefix}.txt',
        kind: 'file',
        status: 'pending',
        action: {
          type: 'file.write',
          path: '${var.output_prefix}.txt',
          content: 'dismissed=${var.dismissed_popup_count}; count=${var.popup_safe_item_count}; first=${var.first_popup_safe_item}',
          outputVariable: 'popup_safe_report_path',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 570)
    ],
    edges: chainEdges(['start', 'open-popup-page', 'dismiss-popups', 'extract-popup-safe', 'write-popup-safe', 'end'])
  },
  {
    category: 'interaction',
    id: 'login-browser',
    name: '登录后提取',
    description: '适合后台、CRM、ERP 等需要登录后点击再提取的页面。',
    variables: [
      variable('login_url', 'https://example.com/login'),
      variable('username', 'demo_user'),
      variable('password', 'demo_password', true)
    ],
    nodes: [
      startNode(),
      stepNode('open', 500, 100, {
        title: '打开登录页',
        description: '${var.login_url}',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.open', targetUrl: '${var.login_url}', timeoutMs: 30_000 }
      }),
      stepNode('fill-user', 500, 205, {
        title: '输入账号',
        description: '#username',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.fill', selector: '#username', inputValue: '${var.username}', timeoutMs: 30_000 }
      }),
      stepNode('fill-pass', 500, 310, {
        title: '输入密码',
        description: '#password',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.fill', selector: '#password', inputValue: '${var.password}', timeoutMs: 30_000 }
      }),
      stepNode('click-login', 500, 415, {
        title: '点击登录',
        description: '#submit',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.click', selector: '#submit', timeoutMs: 30_000 }
      }),
      stepNode('extract', 500, 520, {
        title: '提取结果',
        description: '.result',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.extract',
          selector: '.result',
          outputVariable: 'browser_texts',
          firstValueVariable: 'browser_text',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 640)
    ],
    edges: chainEdges(['start', 'open', 'fill-user', 'fill-pass', 'click-login', 'extract', 'end'])
  },
  {
    category: 'basic',
    id: 'search-results',
    name: '搜索结果采集',
    description: '适合先输入关键词提交搜索，再等待结果列表刷新并提取。',
    variables: [
      variable('search_url', 'https://en.wikipedia.org/wiki/Main_Page'),
      variable('search_keyword', 'Albert Einstein'),
      variable('submit_key', 'Enter')
    ],
    nodes: [
      startNode(),
      stepNode('open-search', 500, 90, {
        title: '打开搜索页',
        description: '${var.search_url}',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.open', targetUrl: '${var.search_url}', timeoutMs: 30_000 }
      }),
      stepNode('fill-search', 500, 210, {
        title: '输入搜索词',
        description: 'input[name="search"]',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.fill', selector: 'input[name="search"]', inputValue: '${var.search_keyword}', timeoutMs: 30_000 }
      }),
      stepNode('press-enter', 500, 330, {
        title: '提交搜索',
        description: '${var.submit_key}',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.press',
          selector: 'input[name="search"]',
          inputValue: '${var.submit_key}',
          outputVariable: 'search_submit_key',
          timeoutMs: 30_000
        }
      }),
      stepNode('wait-results', 500, 450, {
        title: '等待搜索结果',
        description: '#firstHeading',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.wait', selector: '#firstHeading', timeoutMs: 30_000 }
      }),
      stepNode('extract-results', 500, 570, {
        title: '提取搜索结果',
        description: '#firstHeading',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.extract',
          selector: '#firstHeading::text',
          outputVariable: 'search_results',
          firstValueVariable: 'first_search_result',
          countVariable: 'search_result_count',
          timeoutMs: 30_000
        }
      }),
      stepNode('write-search', 500, 690, {
        title: '写入搜索摘要',
        description: '${var.output_prefix}.txt',
        kind: 'file',
        status: 'pending',
        action: {
          type: 'file.write',
          path: '${var.output_prefix}.txt',
          content: 'keyword=${var.search_keyword}; count=${var.search_result_count}; first=${var.first_search_result}',
          outputVariable: 'search_report_path',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 810)
    ],
    edges: chainEdges(['start', 'open-search', 'fill-search', 'press-enter', 'wait-results', 'extract-results', 'write-search', 'end'])
  },
  {
    category: 'basic',
    id: 'api-json',
    name: 'HTTP JSON 抓取',
    description: '适合接口采集、JSON 解析、字段落库前清洗。',
    variables: [variable('api_url', 'https://api.example.com/orders'), variable('trace_id', 'trace-001')],
    nodes: [
      startNode(),
      stepNode('api', 500, 105, {
        title: '请求接口',
        description: 'GET ${var.api_url}',
        kind: 'script',
        status: 'pending',
        action: {
          type: 'http.request',
          method: 'GET',
          url: '${var.api_url}',
          headers: { 'x-trace-id': '${var.trace_id}' },
          responseVariable: 'api_response',
          statusVariable: 'api_status',
          jsonVariable: 'api_json',
          timeoutMs: 30_000
        }
      }),
      stepNode('parse', 500, 225, {
        title: '解析 JSON',
        description: 'api_response → parsed_json',
        kind: 'data',
        status: 'pending',
        action: {
          type: 'data.json.parse',
          inputVariable: 'api_response',
          outputVariable: 'parsed_json',
          countVariable: 'json_count',
          timeoutMs: 30_000
        }
      }),
      stepNode('log', 500, 345, {
        title: '输出状态',
        description: 'HTTP ${var.api_status}',
        kind: 'variable',
        status: 'pending',
        action: { type: 'variable.log', message: 'HTTP ${var.api_status}; count=${var.json_count}', logLevel: 'info' }
      }),
      endNode(500, 465)
    ],
    edges: chainEdges(['start', 'api', 'parse', 'log', 'end'])
  },
  {
    category: 'pagination',
    id: 'pagination-list',
    name: '分页列表抓取',
    description: '适合搜索结果、商品列表等分页场景，按页码打开页面并提取列表。',
    variables: [variable('list_url_template', 'https://example.com/search?q=demo&page=${var.page_no}'), listVariable('page_numbers', [1, 2, 3])],
    nodes: [
      startNode(),
      stepNode('foreach', 500, 95, {
        title: '遍历页码',
        description: 'page_numbers → page_no',
        kind: 'control',
        status: 'pending',
        action: {
          type: 'control.foreach',
          itemsVariable: 'page_numbers',
          itemVariable: 'page_no',
          indexVariable: 'page_index',
          maxIterations: 10,
          timeoutMs: 30_000
        }
      }),
      stepNode('open-page', 260, 215, {
        title: '打开当前页',
        description: '${var.list_url_template}',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.open', targetUrl: '${var.list_url_template}', timeoutMs: 30_000 }
      }),
      stepNode('extract', 260, 335, {
        title: '提取当前页列表',
        description: '.result-item',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.extract',
          selector: '.result-item',
          outputVariable: 'page_items',
          appendVariable: 'all_page_items',
          firstValueVariable: 'first_page_item',
          countVariable: 'page_item_count',
          timeoutMs: 30_000
        }
      }),
      stepNode('write', 500, 455, {
        title: '写入分页摘要',
        description: '${var.output_prefix}.txt',
        kind: 'file',
        status: 'pending',
        action: {
          type: 'file.write',
          path: '${var.output_prefix}.txt',
          content: 'lastPage=${var.page_no}; first=${var.first_page_item}; count=${var.page_item_count}',
          outputVariable: 'pagination_report_path',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 575)
    ],
    edges: [
      ...chainEdges(['start', 'foreach']),
      edge('foreach', 'open-page', '循环体'),
      edge('open-page', 'extract'),
      edge('extract', 'foreach', 'loop', true),
      edge('foreach', 'write', '完成'),
      edge('write', 'end')
    ]
  },
  {
    category: 'interaction',
    id: 'list-detail-browser',
    name: '列表详情链路',
    description: '适合先提取列表链接，再逐条打开详情页抓取内容的网页场景。',
    variables: [variable('list_url', 'https://example.com/articles')],
    nodes: [
      startNode(),
      stepNode('open-list', 500, 90, {
        title: '打开列表页',
        description: '${var.list_url}',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.open', targetUrl: '${var.list_url}', timeoutMs: 30_000 }
      }),
      stepNode('extract-links', 500, 205, {
        title: '提取详情链接',
        description: 'a.article-link[href]',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.extract',
          selector: 'a.article-link',
          extractMode: 'attribute',
          attribute: 'href',
          outputVariable: 'detail_links',
          firstValueVariable: 'first_detail_link',
          countVariable: 'detail_link_count',
          timeoutMs: 30_000
        }
      }),
      stepNode('foreach', 500, 325, {
        title: '遍历详情链接',
        description: 'detail_links → detail_url',
        kind: 'control',
        status: 'pending',
        action: {
          type: 'control.foreach',
          itemsVariable: 'detail_links',
          itemVariable: 'detail_url',
          indexVariable: 'detail_index',
          maxIterations: 50,
          timeoutMs: 30_000
        }
      }),
      stepNode('open-detail', 260, 445, {
        title: '打开详情页',
        description: '${var.detail_url}',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.open', targetUrl: '${var.detail_url}', timeoutMs: 30_000 }
      }),
      stepNode('extract-detail', 260, 565, {
        title: '提取详情正文',
        description: 'article',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.extract',
          selector: 'article',
          outputVariable: 'detail_texts',
          appendVariable: 'all_detail_records',
          appendMode: 'record',
          firstValueVariable: 'last_detail_text',
          countVariable: 'detail_text_count',
          timeoutMs: 30_000
        }
      }),
      stepNode('write', 500, 685, {
        title: '写入最后详情',
        description: '${var.output_prefix}.txt',
        kind: 'file',
        status: 'pending',
        action: {
          type: 'file.write',
          path: '${var.output_prefix}.txt',
          content: '${var.detail_index}:${var.detail_url}\\n${var.last_detail_text}',
          outputVariable: 'detail_report_path',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 805)
    ],
    edges: [
      ...chainEdges(['start', 'open-list', 'extract-links', 'foreach']),
      edge('foreach', 'open-detail', '循环体'),
      edge('open-detail', 'extract-detail'),
      edge('extract-detail', 'foreach', 'loop', true),
      edge('foreach', 'write', '完成'),
      edge('write', 'end')
    ]
  },
  {
    category: 'pagination',
    id: 'next-button-pagination',
    name: '下一页按钮分页',
    description: '适合表格/列表通过“下一页”按钮翻页并累计抓取的页面。',
    variables: [variable('next_page_url', 'https://example.com/orders')],
    nodes: [
      startNode(),
      stepNode('open-next-page', 500, 90, {
        title: '打开分页列表',
        description: '${var.next_page_url}',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.open', targetUrl: '${var.next_page_url}', timeoutMs: 30_000 }
      }),
      stepNode('paginate-next', 500, 210, {
        title: '下一页累计抓取',
        description: 'a.next → .table-row',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.paginateNext',
          selector: 'a.next',
          targetSelector: '.table-row::text',
          extractMode: 'text',
          maxIterations: 20,
          delayMs: 600,
          outputVariable: 'paged_items',
          appendVariable: 'all_paged_items',
          firstValueVariable: 'first_paged_item',
          countVariable: 'paged_item_count',
          pageCountVariable: 'visited_page_count',
          timeoutMs: 45_000
        }
      }),
      stepNode('write-next-page', 500, 330, {
        title: '写入分页结果',
        description: '${var.output_prefix}.txt',
        kind: 'file',
        status: 'pending',
        action: {
          type: 'file.write',
          path: '${var.output_prefix}.txt',
          content: 'pages=${var.visited_page_count}; count=${var.paged_item_count}; first=${var.first_paged_item}',
          outputVariable: 'next_pagination_report_path',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 450)
    ],
    edges: chainEdges(['start', 'open-next-page', 'paginate-next', 'write-next-page', 'end'])
  },
  {
    category: 'pagination',
    id: 'infinite-scroll',
    name: '无限滚动抓取',
    description: '适合瀑布流、动态加载列表，按轮次滚动页面并提取当前列表。',
    variables: [variable('scroll_url', 'https://example.com/feed'), listVariable('scroll_rounds', [1, 2, 3, 4, 5])],
    nodes: [
      startNode(),
      stepNode('open-scroll', 500, 90, {
        title: '打开瀑布流页面',
        description: '${var.scroll_url}',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.open', targetUrl: '${var.scroll_url}', timeoutMs: 30_000 }
      }),
      stepNode('foreach-scroll', 500, 210, {
        title: '遍历滚动轮次',
        description: 'scroll_rounds → scroll_round',
        kind: 'control',
        status: 'pending',
        action: {
          type: 'control.foreach',
          itemsVariable: 'scroll_rounds',
          itemVariable: 'scroll_round',
          indexVariable: 'scroll_index',
          maxIterations: 20,
          timeoutMs: 30_000
        }
      }),
      stepNode('scroll-page', 260, 330, {
        title: '向下滚动页面',
        description: 'distance 1200',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.scroll', distance: 1200, timeoutMs: 30_000 }
      }),
      stepNode('wait-loaded', 260, 450, {
        title: '等待新内容',
        description: '.feed-item',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.wait', selector: '.feed-item', timeoutMs: 30_000 }
      }),
      stepNode('extract-scroll', 260, 570, {
        title: '提取当前列表',
        description: '.feed-item',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.extract',
          selector: '.feed-item',
          outputVariable: 'scroll_items',
          appendVariable: 'all_scroll_items',
          firstValueVariable: 'first_scroll_item',
          countVariable: 'scroll_item_count',
          timeoutMs: 30_000
        }
      }),
      stepNode('write-scroll', 500, 690, {
        title: '写入滚动摘要',
        description: '${var.output_prefix}.txt',
        kind: 'file',
        status: 'pending',
        action: {
          type: 'file.write',
          path: '${var.output_prefix}.txt',
          content: 'round=${var.scroll_round}; index=${var.scroll_index}; first=${var.first_scroll_item}; count=${var.scroll_item_count}',
          outputVariable: 'scroll_report_path',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 810)
    ],
    edges: [
      ...chainEdges(['start', 'open-scroll', 'foreach-scroll']),
      edge('foreach-scroll', 'scroll-page', '循环体'),
      edge('scroll-page', 'wait-loaded'),
      edge('wait-loaded', 'extract-scroll'),
      edge('extract-scroll', 'foreach-scroll', 'loop', true),
      edge('foreach-scroll', 'write-scroll', '完成'),
      edge('write-scroll', 'end')
    ]
  },
  {
    category: 'pagination',
    id: 'click-load-more',
    name: '加载更多抓取',
    description: '适合点击“加载更多/下一批”后追加列表内容的页面。',
    variables: [variable('load_more_url', 'https://example.com/products')],
    nodes: [
      startNode(),
      stepNode('open-load-more', 500, 90, {
        title: '打开列表页',
        description: '${var.load_more_url}',
        kind: 'browser',
        status: 'pending',
        action: { type: 'browser.open', targetUrl: '${var.load_more_url}', timeoutMs: 30_000 }
      }),
      stepNode('click-load-more', 500, 210, {
        title: '点击加载更多',
        description: 'button.load-more → .product-card',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.clickLoadMore',
          selector: 'button.load-more',
          targetSelector: '.product-card::text',
          extractMode: 'text',
          maxIterations: 5,
          delayMs: 800,
          outputVariable: 'loaded_items',
          appendVariable: 'all_loaded_items',
          firstValueVariable: 'first_loaded_item',
          countVariable: 'loaded_item_count',
          loadedCountVariable: 'loaded_dom_count',
          timeoutMs: 30_000
        }
      }),
      stepNode('write-load-more', 500, 330, {
        title: '写入加载结果',
        description: '${var.output_prefix}.txt',
        kind: 'file',
        status: 'pending',
        action: {
          type: 'file.write',
          path: '${var.output_prefix}.txt',
          content: 'domCount=${var.loaded_dom_count}; extracted=${var.loaded_item_count}; first=${var.first_loaded_item}',
          outputVariable: 'load_more_report_path',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 450)
    ],
    edges: chainEdges(['start', 'open-load-more', 'click-load-more', 'write-load-more', 'end'])
  },
  {
    category: 'loop',
    id: 'csv-loop-detail',
    name: 'CSV 循环详情采集',
    description: '适合读取 CSV 列表，按行拼接详情页 URL 并循环采集。',
    variables: [],
    nodes: [
      startNode(),
      stepNode('read', 500, 95, {
        title: '读取订单 CSV',
        description: 'data/orders.csv',
        kind: 'excel',
        status: 'pending',
        action: {
          type: 'excel.read',
          path: 'data/orders.csv',
          outputVariable: 'excel_rows',
          firstValueVariable: 'first_order_id',
          countVariable: 'row_count',
          timeoutMs: 30_000
        }
      }),
      stepNode('foreach', 500, 215, {
        title: '遍历每一行',
        description: 'excel_rows → current_row',
        kind: 'control',
        status: 'pending',
        action: {
          type: 'control.foreach',
          itemsVariable: 'excel_rows',
          itemVariable: 'current_row',
          indexVariable: 'loop_index',
          maxIterations: 1000,
          timeoutMs: 30_000
        }
      }),
      stepNode('fetch', 260, 335, {
        title: '采集详情页',
        description: 'order/${var.current_row.order_id}',
        kind: 'browser',
        status: 'pending',
        action: {
          type: 'browser.fetch',
          targetUrl: 'https://example.com/order/${var.current_row.order_id}',
          selector: '.order-detail::text',
          fetcher: 'dynamic',
          extractMode: 'text',
          outputVariable: 'detail_values',
          appendVariable: 'all_order_details',
          appendMode: 'record',
          firstValueVariable: 'last_detail',
          countVariable: 'detail_count',
          timeoutMs: 45_000
        }
      }),
      stepNode('write', 500, 455, {
        title: '写入最后结果',
        description: '${var.output_prefix}.txt',
        kind: 'file',
        status: 'pending',
        action: {
          type: 'file.write',
          path: '${var.output_prefix}.txt',
          content: '${var.current_row.order_id}:${var.last_detail}',
          outputVariable: 'last_report_path',
          timeoutMs: 30_000
        }
      }),
      endNode(500, 575)
    ],
    edges: [
      ...chainEdges(['start', 'read', 'foreach']),
      edge('foreach', 'fetch', '循环体'),
      edge('fetch', 'foreach', 'loop', true),
      edge('foreach', 'write', '完成'),
      edge('write', 'end')
    ]
  }
];

export function cloneFlowTemplate(template: FlowTemplate): Pick<FlowTemplate, 'edges' | 'nodes' | 'variables'> {
  return {
    edges: template.edges.map((edgeItem) => ({ ...edgeItem, data: cloneRecord(edgeItem.data), markerEnd: cloneRecord(edgeItem.markerEnd), style: cloneRecord(edgeItem.style) })),
    nodes: template.nodes.map((node) => ({ ...node, data: cloneNodeData(node.data), position: { ...node.position } })),
    variables: template.variables.map((item) => ({ ...item }))
  };
}

function startNode(): Node<RpaNodeData> {
  return {
    id: 'start',
    type: 'startEnd',
    position: { x: 560, y: 20 },
    data: { title: '开始', description: 'manual trigger', kind: 'control', status: 'done' }
  };
}

function endNode(x: number, y: number): Node<RpaNodeData> {
  return {
    id: 'end',
    type: 'startEnd',
    position: { x, y },
    data: { title: '结束', description: 'done', kind: 'control', status: 'pending' }
  };
}

function stepNode(id: string, x: number, y: number, data: RpaNodeData): Node<RpaNodeData> {
  return { id, type: 'rpaStep', position: { x, y }, data };
}

function variable(name: string, value: string, sensitive = false): RuntimeVariable {
  return { category: sensitive ? 'credential' : 'flow', name, sensitive, scope: '全局', type: 'String', value };
}

function listVariable(name: string, value: unknown[]): RuntimeVariable {
  return { category: 'flow', name, sensitive: false, scope: '全局', type: 'List', value: JSON.stringify(value) };
}

function chainEdges(ids: string[]): Edge[] {
  return ids.slice(0, -1).map((source, index) => edge(source, ids[index + 1] ?? 'end'));
}

function edge(source: string, target: string, label?: string, loop = false): Edge {
  return {
    id: `e-${source}-${target}${label === undefined ? '' : `-${label}`}`,
    source,
    target,
    type: 'smoothstep',
    label,
    markerEnd: loop ? { type: MarkerType.ArrowClosed, color: '#f59e0b' } : markerEnd,
    style: loop ? { stroke: '#f59e0b', strokeDasharray: '5 4', strokeWidth: 1.5 } : edgeStyle
  };
}

function cloneNodeData(data: RpaNodeData): RpaNodeData {
  return {
    ...data,
    action: data.action === undefined ? undefined : cloneRecord(data.action)
  };
}

function cloneRecord<T>(value: T): T {
  return value === undefined ? value : JSON.parse(JSON.stringify(value)) as T;
}
