import { serve } from 'inngest/edge';

import { inngest } from '../inngest/client.js';
import { automationRouter } from '../inngest/functions/automationRouter.js';
import { nightlyGraphClustering } from '../inngest/functions/nightlyGraphClustering.js';

export const runtime = 'edge';

export default serve({
  client: inngest,
  functions: [nightlyGraphClustering, automationRouter],
});
