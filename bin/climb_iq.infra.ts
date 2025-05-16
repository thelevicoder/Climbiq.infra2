#!/usr/bin/env node
import * as cdk from 'aws-cdk-lib';
import { ClimbIQStack } from '../lib/ClimbIQStack';

const app = new cdk.App();
new ClimbIQStack(app, 'ClimbIQStack');
