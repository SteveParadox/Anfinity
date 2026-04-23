import { serve } from 'inngest/edge';

import { inngest } from '../inngest/client';
import { automationRouter } from '../inngest/functions/automationRouter';
import { nightlyGraphClustering } from '../inngest/functions/nightlyGraphClustering';

export const runtime = 'edge';

export default serve({
  client: inngest,
  functions: [nightlyGraphClustering, automationRouter],
});
