import * as cdk from 'aws-cdk-lib';
import { Stack, StackProps, Duration, RemovalPolicy } from 'aws-cdk-lib';
import { DockerImageFunction, DockerImageCode } from 'aws-cdk-lib/aws-lambda';
import * as awsLambda from 'aws-cdk-lib/aws-lambda';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import * as path from 'path';

export class ClimbIQStack extends Stack {
  constructor(scope: Construct, id: string, props?: StackProps) {
    super(scope, id, props);

    // UserData table
    const tableName = 'UserData';
    const tableArn = `arn:aws:dynamodb:${this.region}:${this.account}:table/${tableName}`;

    // 1) History storage: S3 bucket + DynamoDB table
    const historyBucket = new s3.Bucket(this, 'ClimbIQHistoryBucket', {
      cors: [{
        allowedMethods: [s3.HttpMethods.GET],
        allowedOrigins: ['*'],
        allowedHeaders: ['*']
      }]
    });

    const historyTable = new dynamodb.Table(this, 'ClimbIQHistoryTable', {
      partitionKey: { name: 'id', type: dynamodb.AttributeType.STRING },
      sortKey:      { name: 'timestamp', type: dynamodb.AttributeType.NUMBER },
      removalPolicy: RemovalPolicy.DESTROY
    });

    // 👤 Login Lambda
    const userHandler = new awsLambda.Function(this, 'ClimbIQLoginHandler', {
      runtime: awsLambda.Runtime.NODEJS_18_X,
      handler: 'ClimbIQLogin.handler',
      code: awsLambda.Code.fromAsset(path.join(__dirname, 'handlers')),
      environment: { TABLE_NAME: tableName }
    });
    userHandler.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:Scan'],
      resources: [tableArn]
    }));

    // Presigned S3 upload URL Lambda
    const generateUploadUrlLambda = new awsLambda.Function(this, 'GenerateUploadUrlLambda', {
      runtime: awsLambda.Runtime.NODEJS_18_X,
      handler: 'generate_upload_url_lambda.lambdaHandler',
      code: awsLambda.Code.fromAsset(path.join(__dirname, 'handlers')),
      environment: {
        IMAGE_BUCKET: historyBucket.bucketName,
      },
      timeout: Duration.seconds(10)
    });

    // 🐳 Grading Lambdas (Docker images)
    const contourLambda = new DockerImageFunction(this, 'ContourLambda', {
      code: DockerImageCode.fromImageAsset(path.join(__dirname, '..', 'lambda-docker', 'contour')),
      memorySize: 2048,
      timeout: Duration.seconds(30)
    });

    const gradeHoldLambda = new DockerImageFunction(this, 'GradeHoldLambda', {
      code: DockerImageCode.fromImageAsset(path.join(__dirname, '..', 'lambda-docker', 'hold')),
      memorySize: 2048,
      timeout: Duration.seconds(30)
    });

    const gradeRouteLambda = new DockerImageFunction(this, 'GradeRouteLambda', {
      code: DockerImageCode.fromImageAsset(path.join(__dirname, '..', 'lambda-docker', 'route')),
      memorySize: 2048,
      timeout: Duration.seconds(30)
    });

    // Grant history write permissions to route lambda
    gradeRouteLambda.addEnvironment('IMAGE_BUCKET', historyBucket.bucketName);
    gradeRouteLambda.addEnvironment('HISTORY_TABLE', historyTable.tableName);
    historyBucket.grantPut(generateUploadUrlLambda);
    historyBucket.grantPut(gradeRouteLambda);
    historyTable.grantWriteData(gradeRouteLambda);

    // Lambda for grading by S3 key (process-image-lambda)
    const processImageLambda = new awsLambda.Function(this, 'ProcessImageLambda', {
      runtime: awsLambda.Runtime.NODEJS_18_X,
      handler: 'process_image_lambda.lambdaHandler',
      code: awsLambda.Code.fromAsset(path.join(__dirname, 'handlers')),
      memorySize: 2048,
      timeout: Duration.seconds(30),
      environment: {
        IMAGE_BUCKET: historyBucket.bucketName,
        HISTORY_TABLE: historyTable.tableName,
      },
    });
    historyBucket.grantRead(processImageLambda);
    historyTable.grantReadData(processImageLambda);

    // 🌐 API Gateway
    const api = new apigateway.RestApi(this, 'ClimbIQApi', {
      restApiName: 'ClimbIQ Service',
      description: 'Handles user login, grading, and history',
      deployOptions: { stageName: 'prod' }
    });

    // 👤 /user and /users
    const user = api.root.addResource('user');
    user.addMethod('GET', new apigateway.LambdaIntegration(userHandler));
    user.addMethod('POST', new apigateway.LambdaIntegration(userHandler));

    const users = api.root.addResource('users');
    users.addMethod('GET', new apigateway.LambdaIntegration(userHandler));

    // 🧩 /grade/contour, /grade/hold, /grade/route
    const grade = api.root.addResource('grade');
    const contour = grade.addResource('contour');
    contour.addMethod('POST', new apigateway.LambdaIntegration(contourLambda));
    const hold = grade.addResource('hold');
    hold.addMethod('POST', new apigateway.LambdaIntegration(gradeHoldLambda));
    const route = grade.addResource('route');
    route.addMethod('POST', new apigateway.LambdaIntegration(gradeRouteLambda));

    // 📜 /history endpoint
    const history = api.root.addResource('history');
    const historyLambda = new awsLambda.Function(this, 'HistoryLambda', {
      runtime: awsLambda.Runtime.NODEJS_18_X,
      handler: 'history_lambda.lambdaHandler',
      code: awsLambda.Code.fromAsset(path.join(__dirname, 'handlers')),
      environment: {
        IMAGE_BUCKET: historyBucket.bucketName,
        HISTORY_TABLE: historyTable.tableName,
      },
    });
    historyBucket.grantRead(historyLambda);
    historyTable.grantReadData(historyLambda);
    history.addMethod('GET', new apigateway.LambdaIntegration(historyLambda));

    // /generate-upload-url endpoint for presigned S3 uploads
    const generateUploadUrl = api.root.addResource('generate-upload-url');
    generateUploadUrl.addMethod('POST', new apigateway.LambdaIntegration(generateUploadUrlLambda));

    // /process endpoint for image grading by s3Key
    const processResource = api.root.addResource('process');
    processResource.addMethod('POST', new apigateway.LambdaIntegration(processImageLambda));

    // 🌍 CORS for all resources
    const mockIntegration = new apigateway.MockIntegration({
      integrationResponses: [{
        statusCode: '200',
        responseParameters: {
          'method.response.header.Access-Control-Allow-Headers': "'Content-Type'",
          'method.response.header.Access-Control-Allow-Methods': "'OPTIONS,GET,POST'",
          'method.response.header.Access-Control-Allow-Origin': "'*'"
        }
      }],
      passthroughBehavior: apigateway.PassthroughBehavior.NEVER,
      requestTemplates: { 'application/json': '{"statusCode": 200}' }
    });

    const corsOptions: apigateway.MethodOptions = {
      methodResponses: [{
        statusCode: '200',
        responseParameters: {
          'method.response.header.Access-Control-Allow-Headers': true,
          'method.response.header.Access-Control-Allow-Methods': true,
          'method.response.header.Access-Control-Allow-Origin': true
        }
      }]
    };

    [user, users, contour, hold, route, history, generateUploadUrl, processResource].forEach(res => {
      res.addMethod('OPTIONS', mockIntegration, corsOptions);
    });
  }
}
