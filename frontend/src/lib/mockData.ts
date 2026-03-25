import type { User, Note, AIInsight, Workspace, KnowledgeNode, KnowledgeLink } from '@/types';

export const mockUser: User = {
  id: 'user-1',
  name: 'Alex Chen',
  email: 'alex@example.com',
  plan: 'pro',
};

export const mockNotes: Note[] = [
  {
    id: 'note-1',
    title: 'AI Agent Architecture Patterns',
    content: 'Modern AI agents follow several key architectural patterns:\n\n1. **ReAct Pattern**: Reasoning and Acting in language models. The agent alternates between thinking (reasoning) and taking actions (using tools).\n\n2. **Plan-and-Execute**: The agent first creates a plan, then executes each step systematically.\n\n3. **Multi-Agent Systems**: Multiple specialized agents collaborate, each with different capabilities.\n\n4. **Reflection Pattern**: Agents self-critique and improve their outputs through iterative refinement.\n\nKey considerations:\n- Tool selection and description clarity\n- Error handling and recovery\n- Context window management\n- Cost optimization through caching',
    summary: 'Overview of AI agent architecture patterns including ReAct, Plan-and-Execute, Multi-Agent Systems, and Reflection patterns with implementation considerations.',
    tags: ['AI', 'Architecture', 'LLM', 'Agents'],
    createdAt: new Date('2025-02-15T10:30:00'),
    updatedAt: new Date('2025-02-18T14:22:00'),
    userId: 'user-1',
    workspaceId: 'ws-1',
    source: 'https://research.openai.com/agents',
    confidence: 0.94,
    connections: ['note-2', 'note-5'],
    type: 'note',
  },
  {
    id: 'note-2',
    title: 'Retrieval-Augmented Generation (RAG) Best Practices',
    content: 'RAG systems combine retrieval mechanisms with language models to provide grounded, factual responses.\n\n**Chunking Strategies:**\n- Fixed-size chunking with overlap\n- Semantic chunking based on content boundaries\n- Agentic chunking with LLM-based segmentation\n\n**Embedding Models:**\n- text-embedding-3-large for high quality\n- Multi-modal embeddings for images + text\n- Fine-tuned domain-specific embeddings\n\n**Retrieval Optimization:**\n- Hybrid search (dense + sparse)\n- Re-ranking with cross-encoders\n- Query expansion and rewriting\n\n**Evaluation Metrics:**\n- Context precision and recall\n- Answer relevance\n- Faithfulness to sources',
    summary: 'Comprehensive guide to RAG implementation covering chunking strategies, embedding models, retrieval optimization, and evaluation metrics.',
    tags: ['RAG', 'LLM', 'Vector DB', 'Embeddings'],
    createdAt: new Date('2025-02-14T09:15:00'),
    updatedAt: new Date('2025-02-17T11:45:00'),
    userId: 'user-1',
    workspaceId: 'ws-1',
    confidence: 0.91,
    connections: ['note-1', 'note-3'],
    type: 'note',
  },
  {
    id: 'note-3',
    title: 'Vector Database Comparison 2025',
    content: 'Comparing leading vector databases for production AI applications:\n\n**Pinecone**\n- Fully managed, serverless\n- Excellent metadata filtering\n- Strong hybrid search\n- Price: $0.10/GB/month\n\n**Weaviate**\n- Open-source option\n- GraphQL interface\n- Built-in vectorization\n- Good for multi-modal\n\n**Chroma**\n- Developer-friendly\n- Easy local development\n- Limited scale\n\n**Milvus/Zilliz**\n- High performance at scale\n- GPU acceleration\n- Complex setup\n\n**Recommendation**: Start with Pinecone for production, Chroma for prototyping.',
    summary: 'Detailed comparison of vector databases including Pinecone, Weaviate, Chroma, and Milvus with pricing and use case recommendations.',
    tags: ['Vector DB', 'Database', 'AI Infrastructure'],
    createdAt: new Date('2025-02-13T16:20:00'),
    updatedAt: new Date('2025-02-16T10:00:00'),
    userId: 'user-1',
    workspaceId: 'ws-1',
    source: 'https://pinecone.io/blog/comparison',
    confidence: 0.88,
    connections: ['note-2'],
    type: 'web-clip',
  },
  {
    id: 'note-4',
    title: 'Product Strategy: Knowledge Management Market',
    content: 'Knowledge Management Software Market Analysis:\n\n**Market Size**\n- 2025: $23.2B\n- 2034: $74.2B (projected)\n- CAGR: 13.8%\n\n**Key Trends**\n- AI-powered knowledge automation\n- Cloud-native solutions (62% market share)\n- Intelligent chatbots (21.88% CAGR)\n- Trust deficit in AI-generated content\n\n**Market Gaps**\n- End-user trust issues with AI\n- High implementation complexity\n- Data security concerns\n- Shortage of knowledge engineering talent\n\n**Opportunity**\nBuild trust-first AI knowledge platform with:\n- Source attribution\n- Confidence scoring\n- Human verification workflows\n- Zero-config setup',
    summary: 'Market analysis of knowledge management software identifying key trends, gaps, and opportunities for a trust-first AI platform.',
    tags: ['Product Strategy', 'Market Research', 'AI', 'Business'],
    createdAt: new Date('2025-02-12T11:00:00'),
    updatedAt: new Date('2025-02-15T09:30:00'),
    userId: 'user-1',
    workspaceId: 'ws-2',
    confidence: 0.96,
    connections: ['note-6'],
    type: 'note',
  },
  {
    id: 'note-5',
    title: 'LLM Prompt Engineering Techniques',
    content: 'Advanced prompt engineering for better LLM outputs:\n\n**Chain-of-Thought (CoT)**\n"Let\'s think through this step by step..."\nImproves reasoning by 40%\n\n**Few-Shot Learning**\nProvide 3-5 examples before the actual task\n\n**System Prompting**\nDefine persona, constraints, and output format\n\n**Self-Consistency**\nGenerate multiple answers, pick most common\n\n**Tree of Thoughts**\nExplore multiple reasoning paths\n\n**Tool Use / Function Calling**\nStructured outputs for API integration\n\n**Best Practices**\n- Be specific and clear\n- Use delimiters for structure\n- Specify output format (JSON, markdown)\n- Include error handling instructions',
    summary: 'Advanced LLM prompt engineering techniques including Chain-of-Thought, Few-Shot Learning, and Tree of Thoughts with performance improvements.',
    tags: ['LLM', 'Prompt Engineering', 'AI'],
    createdAt: new Date('2025-02-11T14:30:00'),
    updatedAt: new Date('2025-02-14T16:45:00'),
    userId: 'user-1',
    workspaceId: 'ws-1',
    source: 'https://arxiv.org/abs/2201.11903',
    confidence: 0.92,
    connections: ['note-1'],
    type: 'note',
  },
  {
    id: 'note-6',
    title: 'SaaS Pricing Strategy Framework',
    content: 'Pricing strategy for B2B SaaS products:\n\n**Pricing Models**\n1. Usage-based (AWS, Twilio)\n2. Per-seat (Slack, Notion)\n3. Tiered feature-based (HubSpot)\n4. Hybrid (Stripe)\n\n**Psychology Principles**\n- Anchoring: Show most expensive plan first\n- Decoy effect: Add middle option to push top tier\n- Loss aversion: Highlight what they miss on lower tiers\n\n**Freemium Strategy**\n- 20% feature set free\n- Core value in paid tiers\n- Clear upgrade triggers\n\n**Enterprise Pricing**\n- Custom pricing (don\'t show on website)\n- Annual contracts with discounts\n- Professional services add-on\n\n**Key Metrics**\n- LTV/CAC ratio > 3\n- Net Revenue Retention > 110%\n- Gross Margin > 80%',
    summary: 'Comprehensive SaaS pricing framework covering models, psychology principles, freemium strategy, and key metrics for B2B products.',
    tags: ['Pricing', 'SaaS', 'Business', 'Strategy'],
    createdAt: new Date('2025-02-10T09:00:00'),
    updatedAt: new Date('2025-02-13T11:20:00'),
    userId: 'user-1',
    workspaceId: 'ws-2',
    confidence: 0.89,
    connections: ['note-4'],
    type: 'note',
  },
  {
    id: 'note-7',
    title: 'React Performance Optimization',
    content: 'React performance optimization techniques:\n\n**Rendering Optimization**\n- React.memo for pure components\n- useMemo for expensive calculations\n- useCallback for function props\n- Virtualization for long lists\n\n**State Management**\n- Lift state only when necessary\n- Use Zustand/Jotai over Redux for simple cases\n- Split contexts by domain\n\n**Bundle Optimization**\n- Code splitting with React.lazy\n- Tree shaking\n- Dynamic imports\n\n**Network Optimization**\n- React Query for caching\n- Optimistic updates\n- Prefetching\n\n**Profiling Tools**\n- React DevTools Profiler\n- Lighthouse\n- Web Vitals',
    summary: 'React performance optimization guide covering rendering, state management, bundle optimization, and profiling tools.',
    tags: ['React', 'Performance', 'Frontend', 'JavaScript'],
    createdAt: new Date('2025-02-09T13:45:00'),
    updatedAt: new Date('2025-02-12T15:30:00'),
    userId: 'user-1',
    workspaceId: 'ws-3',
    confidence: 0.87,
    connections: [],
    type: 'note',
  },
  {
    id: 'note-8',
    title: 'Team Meeting: Q1 Roadmap Discussion',
    content: 'Key decisions from Q1 roadmap meeting:\n\n**Product Priorities**\n1. AI-powered search (P0)\n2. Knowledge graph visualization (P0)\n3. Team collaboration features (P1)\n4. Mobile app (P2)\n\n**Technical Debt**\n- Refactor auth system\n- Upgrade to React 19\n- Migrate to new database\n\n**Hiring Plan**\n- 2 senior engineers\n- 1 product designer\n- 1 customer success\n\n**Budget Allocation**\n- Engineering: 60%\n- Marketing: 25%\n- Operations: 15%\n\nNext meeting: Feb 28',
    summary: 'Q1 roadmap decisions covering product priorities, technical debt, hiring plan, and budget allocation.',
    tags: ['Meeting Notes', 'Roadmap', 'Team'],
    createdAt: new Date('2025-02-08T10:00:00'),
    updatedAt: new Date('2025-02-08T12:00:00'),
    userId: 'user-1',
    workspaceId: 'ws-2',
    confidence: 1.0,
    connections: [],
    type: 'note',
  },
];

export const mockInsights: AIInsight[] = [
  {
    id: 'insight-1',
    content: 'Your notes on AI Agent Architecture and RAG share 3 common concepts. Would you like me to create a connection between them?',
    sources: ['note-1', 'note-2'],
    confidence: 0.92,
    createdAt: new Date('2025-02-19T08:30:00'),
    type: 'connection',
  },
  {
    id: 'insight-2',
    content: 'Based on your market research, I\'ve identified a trend: 62% of enterprises prioritize knowledge-sharing platforms. This aligns with your product strategy.',
    sources: ['note-4'],
    confidence: 0.88,
    createdAt: new Date('2025-02-19T09:15:00'),
    type: 'trend',
  },
  {
    id: 'insight-3',
    content: 'I can summarize your 8 notes about AI/LLM into a comprehensive guide. This would save approximately 45 minutes of reading time.',
    sources: ['note-1', 'note-2', 'note-5'],
    confidence: 0.85,
    createdAt: new Date('2025-02-18T16:45:00'),
    type: 'suggestion',
  },
  {
    id: 'insight-4',
    content: 'Your SaaS pricing framework note could be enhanced with competitor pricing data. Shall I research current market rates?',
    sources: ['note-6'],
    confidence: 0.79,
    createdAt: new Date('2025-02-18T11:20:00'),
    type: 'suggestion',
  },
];

export const mockWorkspaces: Workspace[] = [
  {
    id: 'ws-1',
    name: 'AI Research',
    description: 'Research and notes on AI/ML technologies',
    members: [
      { userId: 'user-1', role: 'owner', joinedAt: new Date('2025-01-01') },
    ],
    createdAt: new Date('2025-01-01'),
    updatedAt: new Date('2025-02-18'),
  },
  {
    id: 'ws-2',
    name: 'Product Strategy',
    description: 'Product roadmap, strategy, and business planning',
    members: [
      { userId: 'user-1', role: 'owner', joinedAt: new Date('2025-01-15') },
    ],
    createdAt: new Date('2025-01-15'),
    updatedAt: new Date('2025-02-15'),
  },
  {
    id: 'ws-3',
    name: 'Engineering',
    description: 'Technical documentation and engineering notes',
    members: [
      { userId: 'user-1', role: 'owner', joinedAt: new Date('2025-01-20') },
    ],
    createdAt: new Date('2025-01-20'),
    updatedAt: new Date('2025-02-12'),
  },
];

export const generateKnowledgeGraph = (notes: Note[]): { nodes: KnowledgeNode[]; links: KnowledgeLink[] } => {
  const nodes: KnowledgeNode[] = [];
  const links: KnowledgeLink[] = [];
  const tagMap = new Map<string, { count: number; noteIds: string[] }>();

  // Extract all tags and their frequencies
  notes.forEach(note => {
    note.tags.forEach(tag => {
      const existing = tagMap.get(tag);
      if (existing) {
        existing.count++;
        existing.noteIds.push(note.id);
      } else {
        tagMap.set(tag, { count: 1, noteIds: [note.id] });
      }
    });
  });

  // Create category colors
  const categoryColors: Record<string, string> = {
    'AI': '#6366f1',
    'LLM': '#8b5cf6',
    'Architecture': '#06b6d4',
    'RAG': '#10b981',
    'Vector DB': '#f59e0b',
    'Product Strategy': '#ec4899',
    'Business': '#f97316',
    'Pricing': '#ef4444',
    'React': '#61dafb',
    'Frontend': '#38bdf8',
    'Meeting Notes': '#84cc16',
    'Team': '#a855f7',
  };

  // Create nodes from tags
  tagMap.forEach((data, tag) => {
    nodes.push({
      id: `tag-${tag}`,
      name: tag,
      val: Math.sqrt(data.count) * 5 + 5,
      color: categoryColors[tag] || '#94a3b8',
      category: tag,
      noteIds: data.noteIds,
    });
  });

  // Create nodes from notes
  notes.forEach(note => {
    nodes.push({
      id: note.id,
      name: note.title.length > 30 ? note.title.substring(0, 30) + '...' : note.title,
      val: 8,
      color: '#e2e8f0',
      category: 'note',
      noteIds: [note.id],
    });
  });

  // Create links between notes and their tags
  notes.forEach(note => {
    note.tags.forEach(tag => {
      links.push({
        source: note.id,
        target: `tag-${tag}`,
        value: 1,
        type: 'related',
      });
    });
  });

  // Create links between tags that appear together
  const tagPairs = new Map<string, number>();
  notes.forEach(note => {
    const tags = note.tags;
    for (let i = 0; i < tags.length; i++) {
      for (let j = i + 1; j < tags.length; j++) {
        const pair = [tags[i], tags[j]].sort().join('-');
        tagPairs.set(pair, (tagPairs.get(pair) || 0) + 1);
      }
    }
  });

  tagPairs.forEach((count, pair) => {
    const [tag1, tag2] = pair.split('-');
    if (count >= 1) {
      links.push({
        source: `tag-${tag1}`,
        target: `tag-${tag2}`,
        value: count,
        type: 'similar',
      });
    }
  });

  // Create links from note connections
  notes.forEach(note => {
    note.connections.forEach(targetId => {
      links.push({
        source: note.id,
        target: targetId,
        value: 2,
        type: 'references',
      });
    });
  });

  return { nodes, links };
};
